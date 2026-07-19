import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  fetchGovernanceGating,
  fetchOntologySummary,
  fetchVitals,
  setApiWorld,
  type GovernanceGating,
  type ObjectRef,
  type OntologyLink,
  type Role,
  type Vitals,
  type DataWindow,
  type World,
  type ZoneId,
} from "./api";
import StateHint from "./components/StateHint";
import TopBar from "./components/TopBar";
import AiWorkflow from "./views/AiWorkflow";
import CommandWall from "./views/CommandWall";
import type { Lane } from "./views/corridorModel";
import ImpactPanel, { type ImpactFocus } from "./views/ImpactPanel";
import LaneQueue from "./views/LaneQueue";
import ObjectCard from "./views/ObjectCard";
import RouteMap from "./views/RouteMap";
import ZoneQueue from "./views/ZoneQueue";
import type { DrillTarget } from "./views/zoneModel";

// 驾驶舱首屏编排（V10 方案 C，Daniel 亲批）：顶栏 + 主舞台（中央 60% · 右栏 AI 工作流 40%）。
// 中央四态（下钻四段式：总览→队列→详情→动作）：
//   ① wall  七区指挥墙（默认首屏，体征带的放大态；顶部体征带已移除避免同信息两处）
//   ② zone  某区工作队列（点区卡进入，面包屑返回）
//   ③ map   履约航线夜景地图（履约卡「航线视图」切入，SAP tile 内切换范式；V10 补记：走廊图升真地图）
//   ④ lane  某航线异常队列（点地图弧线进入）
// 队列条目 → 右栏滑出影响面板（风险类，含动作占位）/ 打开对象卡（对象类）——两资产直接复用。
// 角色（X-Role）是唯一全局开关：切换即令 vitals/panorama/ai-flow 全部按新角色重取（脱敏+粒度），
// 并回到指挥墙、收起详情与对象卡。

type Stage = { view: "wall" } | { view: "zone"; zoneId: ZoneId } | { view: "map" } | { view: "lane"; lane: Lane };

export default function App() {
  const [role, setRole] = useState<Role>("manager");
  // 世界切换（U1；V22④ 2026-07-19 Daniel 批准"演示默认世界改模拟世界"）：初始值直接给 "sim"
  // （不再是 null 跟随 apps/api 启动环境变量）——首屏即带 X-World: sim 头，指挥墙首屏直接落模拟
  // 世界数据。验证世界仍是评估基线世界，未被动摇，只是不再是默认落点；点顶栏「验证世界」钮随时切回
  // （TopBar 常驻渲染，不依赖 vitals 是否取到，体征带报错时也能点，见下方 vitalsErr 分支）。
  // world 状态经 setApiWorld 同步到 api 模块级单例 → 全舱所有 fetch 自动带 X-World 头。
  const [world, setWorld] = useState<World>("sim");
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [vitalsErr, setVitalsErr] = useState(false);
  const [vitalsReload, setVitalsReload] = useState(0); // 体征带重试计数（StateHint 重试按钮驱动）
  const [gating, setGating] = useState<GovernanceGating | null>(null); // U3 AI 信任档（世界/角色无关，取一次）
  const [links, setLinks] = useState<OntologyLink[]>([]);

  // world 状态 → api 模块级单例。用 layout effect：本 effect 在提交阶段（子组件 passive effect 之前）
  // 跑，保证子组件/本组件的 fetch effect 读到的 currentWorld 已是最新，切世界不产生"旧世界头"竞态。
  useLayoutEffect(() => {
    setApiWorld(world);
  }, [world]);
  // 世界时钟时间轴回放（A-2/V13②）：asOf=null 即"今天"（现状不变，不带 as_of 参数、byte-identical）；
  // 拖到过去某天 → 三聚合端点带 as_of 重算（脱敏/回放都在服务端做）。windowRange 单独存，reload
  // 期间（vitals 短暂置 null）滑条不丢定义域、不闪。
  const [asOf, setAsOf] = useState<string | null>(null);
  const [windowRange, setWindowRange] = useState<DataWindow | null>(null);

  const [stage, setStage] = useState<Stage>({ view: "wall" });
  const [detail, setDetail] = useState<ImpactFocus | null>(null); // 右栏影响面板（风险类下钻）
  const [activeKey, setActiveKey] = useState<string | null>(null); // 队列高亮行
  const [card, setCard] = useState<ObjectRef | null>(null); // 对象卡抽屉（对象类下钻）

  const closeDetail = () => {
    setDetail(null);
    setActiveKey(null);
  };

  // 角色切换：回今天（asOf=null）+ 重置导航。setAsOf 与 setRole 同批 → 下方数据 effect 单次跑
  // (role, null)，不双取。
  const changeRole = (r: Role) => {
    setRole(r);
    setAsOf(null);
  };

  // 欠账修复（角色钮太远）：下钻里就地切角色——批准/关闭的灰态里点「切到老板/运营角色」，直接换身份
  // 但**保留当前下钻上下文**（不回指挥墙、不收详情/对象卡），换完就能拍板。与顶栏 changeRole 的区别=
  // 不重置导航、不回今天。靠 preserveNavOnRoleChange 让下方 [role] 的导航重置 effect 这一趟让路（数据
  // effect 仍会因 role 变而重取脱敏，ImpactPanel/ObjectCard 也各自随 role 变刷新，故切完即生效）。
  const preserveNavOnRoleChange = useRef(false);
  const switchRole = (r: Role) => {
    if (r === role) return;
    preserveNavOnRoleChange.current = true;
    setRole(r);
  };

  // 当前生效世界：V22④后 world 状态本身即当前生效世界（默认 "sim"，永不为 null）——不再需要从
  // vitals 反推兜底；旧注释"首屏跟随服务端默认"已随本次改动作废，这里一并订正，避免自相矛盾。
  // 供顶栏切换钮高亮 + changeWorld 判别"点的是不是当前世界"（是则不折腾）。
  const activeWorld: World = world;

  // 世界切换（U1）：切世界=回今天（asOf=null）+ 清全部下钻状态（回指挥墙、收详情与对象卡）——
  // 跨世界的下钻目标 id 不通用，留着会指向错库对象。RouteMap/ObjectCard 因回到指挥墙+清 card 而
  // 卸载，下次打开即用新世界重取，无需逐个改它们的签名。点当前世界=no-op（不做无谓重取与重置）。
  const changeWorld = (w: World) => {
    if (w === activeWorld) return;
    setWorld(w);
    setAsOf(null);
    setStage({ view: "wall" });
    setDetail(null);
    setActiveKey(null);
    setCard(null);
  };

  // 人类决策（批准/驳回）成功后：只重取体征数据（队列随 vitals 刷新，已拍板的提案自动移出待批），
  // 不重置导航——用户停留在待拍板队列，仅收起右栏详情。与角色切换的整屏重置区分开。回放态保持当前 asOf。
  const refreshVitalsData = () => {
    fetchVitals(role, asOf)
      .then((v) => setVitals(v))
      .catch(() => setVitalsErr(true));
  };

  // 体征带数据：角色 / 回放时点 / 世界 变即重取（脱敏 + 回放 + 世界库都在服务端做）。windowRange
  // 只在有值时更新，reload 期间不丢——切到 sim 世界时 window 自动跟随后端下发的 14 个月区间，
  // 时间回放威力随之完整（验证世界 window.start==end → 顶栏退化为静态时钟）。vitalsReload 为
  // StateHint 错误态重试按钮的驱动计数。
  useEffect(() => {
    let cancelled = false;
    setVitals(null);
    setVitalsErr(false);
    fetchVitals(role, asOf)
      .then((v) => {
        if (cancelled) return;
        setVitals(v);
        if (v.window) setWindowRange(v.window);
      })
      .catch(() => !cancelled && setVitalsErr(true));
    return () => {
      cancelled = true;
    };
  }, [role, asOf, world, vitalsReload]);

  // AI 信任档（U3）：治理放权档位摘要，世界/角色无关（离线 gating_report.json），取一次即可。
  // 失败不阻断主画面——CommandWall 侧按 available/null 画诚实空态。
  useEffect(() => {
    let cancelled = false;
    fetchGovernanceGating(role)
      .then((g) => !cancelled && setGating(g))
      .catch(() => !cancelled && setGating(null));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 角色切换重置导航（asOf 已由 changeRole 同批置 null，此处只管导航，不碰数据取回）。
  // 例外：内联 switchRole（下钻里就地切身份）要保留下钻上下文——这一趟让路，只让路一次即复位标志。
  useEffect(() => {
    if (preserveNavOnRoleChange.current) {
      preserveNavOnRoleChange.current = false;
      return;
    }
    setStage({ view: "wall" });
    setDetail(null);
    setActiveKey(null);
    setCard(null);
  }, [role]);

  // 本体关系清单（对象卡 links 列表来源，与角色无关，取一次）
  useEffect(() => {
    let cancelled = false;
    fetchOntologySummary(role)
      .then((o) => !cancelled && setLinks(o.links))
      .catch(() => {
        /* 对象卡打开时若无 links 仅少了关系区，不阻断主画面 */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // —— 导航（每次切换收起右栏详情，回到 AI 工作流）——
  const goWall = () => {
    setStage({ view: "wall" });
    closeDetail();
  };
  const goZone = (zoneId: ZoneId) => {
    // L-UX 轮2 P2：墙卡计数与队列头计数曾各自取数不同鲜（测试期间他处审批后墙面滞留旧值，
    // 22 vs 共20条被读成"漏了2条"）。下钻即刷体征——两处永远同一次取数口径。
    refreshVitalsData();
    setStage({ view: "zone", zoneId });
    closeDetail();
  };
  const goMap = () => {
    setStage({ view: "map" });
    closeDetail();
  };
  const goLane = (lane: Lane) => {
    setStage({ view: "lane", lane });
    closeDetail();
  };

  // —— 下钻：风险类 → 右栏影响面板；对象类 → 对象卡抽屉 ——
  const handleDrill = (t: DrillTarget, key: string) => {
    if (t.kind === "risk") {
      setDetail({
        title: t.title,
        subtitle: t.subtitle,
        alerts: [{ risk_event_id: t.riskId, rule_id: "", type: "", severity: "" }],
        actionHint: t.actionHint,
        decision: t.decision, // 待拍板提案 → 影响面板动作区渲染批准/驳回（A-1）
      });
      setActiveKey(key);
    } else {
      setCard(t.ref);
    }
  };

  const activeZone = stage.view === "zone" && vitals ? vitals.zones.find((z) => z.zone === stage.zoneId) ?? null : null;

  return (
    <div className="cockpit">
      {vitalsErr && (
        <div className="cp-degrade">
          API 未连接或当前世界数据不可用——数据降级。确认 apps/api 服务已在 8100 端口启动：
          <span className="num">uvicorn apps.api.main:app --port 8100</span>；
          {/* V22④ 降级引导：默认世界改模拟世界后，服务已启动但模拟世界库缺失（data/simworld.sqlite
              不存在）也会走到这条错误分支——不是"重启进程"能解的，指路右上角一键切回验证世界脱困。 */}
          若服务已启动但仍报错（如模拟世界库 data/simworld.sqlite 尚未生成），点击右上角「验证世界」
          可切回已确定有数据的世界（详见 apps/cockpit/README.md）。
        </div>
      )}
      <TopBar
        activeWorld={activeWorld}
        onWorld={changeWorld}
        clock={vitals?.clock ?? windowRange?.end ?? null}
        windowRange={windowRange}
        asOf={asOf}
        onAsOf={setAsOf}
        replayNote={vitals?.as_of?.note ?? null}
        role={role}
        onRole={changeRole}
      />

      <div className="cp-stage">
        <div className="cp-center">
          {stage.view === "map" ? (
            <RouteMap role={role} asOf={asOf} onLane={goLane} onBack={goWall} />
          ) : stage.view === "lane" ? (
            <LaneQueue lane={stage.lane} onWall={goWall} onMap={goMap} onDrill={handleDrill} activeKey={activeKey} />
          ) : !vitals ? (
            vitalsErr ? (
              <StateHint
                kind="error"
                title="体征带不可用"
                // V22④：默认世界=模拟世界，若模拟世界库未生成会走到这条错误分支——原文案只提示
                // "确认服务启动"会误导（服务其实在跑，缺的是库文件），补上"切验证世界"这条真实脱困路径。
                message={
                  activeWorld === "sim"
                    ? "没能连上驾驶舱数据接口，或当前「模拟世界」数据库文件缺失。确认 apps/api 服务已在 8100 端口启动；若是模拟世界库未生成，点击右上角「验证世界」切换即可恢复。"
                    : "没能连上驾驶舱数据接口。确认 apps/api 服务已在 8100 端口启动，再重试。"
                }
                onRetry={() => setVitalsReload((n) => n + 1)}
              />
            ) : (
              <StateHint kind="loading" title="指挥墙加载中…" skeletonRows={4} />
            )
          ) : stage.view === "zone" && activeZone ? (
            <ZoneQueue zone={activeZone} onBack={goWall} onMap={goMap} onDrill={handleDrill} activeKey={activeKey} />
          ) : (
            <CommandWall zones={vitals.zones} provenance={vitals.provenance} gating={gating} onZone={goZone} onMap={goMap} />
          )}
        </div>
        <div className="cp-side">
          {detail ? (
            <ImpactPanel
              focus={detail}
              role={role}
              onOpenObject={setCard}
              onClose={closeDetail}
              onActed={() => {
                refreshVitalsData();
                closeDetail();
              }}
              onSwitchRole={switchRole}
            />
          ) : (
            <AiWorkflow role={role} asOf={asOf} world={world} onOpenObject={setCard} />
          )}
        </div>
      </div>

      {card && (
        <ObjectCard
          target={card}
          role={role}
          links={links}
          onOpenObject={setCard}
          onClose={() => setCard(null)}
          onActed={refreshVitalsData} // B-1：准入案批准/驳回后刷新体征（准入漏斗/准入毛利率分布随之变化）
          onSwitchRole={switchRole} // P0/欠账：任务审批灰态里就地切老板角色（保留对象卡不回墙）
        />
      )}
    </div>
  );
}
