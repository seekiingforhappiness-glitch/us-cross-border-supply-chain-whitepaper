"""模拟时钟（时间引擎）：按天 tick 推进，支持起停/调速/跳转；as_of 语义与主系统一致（D8）。

为什么这样建（≤5 行）：① 时钟是纯确定性的日期推进器，不含随机、不含系统时间——回填与
未来的"逐日 live 驱动"共用同一个时钟，保证一套逻辑两种驱动。② as_of 为硬边界：cursor 永不
越过 as_of，一切 iso_dt 产出的时间戳天然 ≤ as_of，无未来泄漏。③ 起停/调速/跳转是给 live/演示
模式的接口（1x/10x/暂停），回填只用 tick_to(as_of)。
"""
from datetime import date, timedelta


class SimClock:
    def __init__(self, start, as_of, speed=1):
        self.start = start
        self.as_of = as_of            # 硬边界：cursor ≤ as_of，一切事件 ≤ as_of
        self.cursor = start
        self.speed = speed            # 调速：1x 实时感 / 10x 快进（给 live/演示；回填不用）
        self.running = True
        self.ticks = 0

    # --- 起停/调速（live/演示接口）---
    def pause(self):
        self.running = False

    def resume(self):
        self.running = True

    def set_speed(self, speed):
        self.speed = max(1, int(speed))

    # --- 推进 ---
    def tick(self):
        """推进一天；返回推进后的日期，或已达 as_of 时返回 None（不越界）。"""
        if not self.running or self.cursor >= self.as_of:
            return None
        self.cursor = self.cursor + timedelta(days=1)
        self.ticks += 1
        return self.cursor

    def jump_to(self, target):
        """跳转到某日（时间旅行/演示回放）；不得越过 as_of。"""
        t = target if isinstance(target, date) else date.fromisoformat(target)
        if t > self.as_of:
            raise ValueError(f"jump_to {t} 越过 as_of {self.as_of}（禁止未来泄漏）")
        self.cursor = max(self.start, t)
        return self.cursor

    def iter_days(self):
        """从 start 到 as_of（含）逐日产出——回填驱动器用；确定性、无随机。"""
        d = self.start
        while d <= self.as_of:
            yield d
            d = d + timedelta(days=1)

    def is_done(self):
        return self.cursor >= self.as_of


def iso_dt(day, hour):
    """事件时间戳（UTC ISO8601，D8 口径）。"""
    return f"{day.isoformat()}T{hour:02d}:00:00Z"
