# -*- coding: utf-8 -*-
"""gen_write_batches_20261008.py — 生成 sheetagent set_cell_range 批次载荷
add_rows: 16 列整行（含 N 转写全文 / O 中文翻译 / P 风格标签）
update_rows: 仅 I-M 动态五列
批次文件: reports/2026-10-08/write_batches/batch_*.json（pretty JSON，单元格值 {value:...}）
"""
import json
from pathlib import Path

REPORTS = Path(r"D:/workbuddy_project/tiktok/pipeline/reports/2026-10-08")
DATE = "2026-10-08"

# ---- O 列中文翻译 & P 列风格标签（人工生成，嵌入脚本留档）----
VIVI_META = {
    "7693728382785195294": {
        "label": "信仰情感共鸣",
        "trans": "这是不是太惊艳了？还有什么比这更好的方式来表达你对主的爱与信仰呢？来一枚这样的耶和华硬币吧。说实话这真的很棒，如果你是硬币收藏爱好者，这绝对是佳品。就这么看着它，放在玻璃展示盒里精美绝伦。如果你的收藏里还没有这一枚，点击橙色购物车吧——它会成为任何硬币收藏的绝佳补充。愿主常与你同在，点击橙色购物车下单吧。",
    },
    "7693762937386831134": {
        "label": "卖点口播强调",
        "trans": "这是真正的14K金，所以永远不会褪色。你可以天天佩戴——洗澡戴、出汗戴、出门办事戴都行，它全都能扛住。颜色不会变，手腕上也不会出现绿色印子或奇怪的污渍。从任何角度看都干净闪亮，拿在手里感觉扎实有质感，而且完美贴合手腕。戴上就是好看。",
    },
    "7693799040059854111": {
        "label": "风格化产品展示",
        "trans": "这里是怎么回事？为什么所有东西都……变成铬色了？未来的一切都是铬色的！（网络热梗配音，主体为鳄鱼皮纹手机壳风格化展示）",
    },
    "7693842456051207455": {
        "label": "口播功能讲解",
        "trans": "这个新勃艮第红最近真的超火。一款手机壳集成多个实用功能：内置隐藏支架，看视频、开会、打游戏都能解放双手；自带镜头保护，不用再额外贴镜头膜；强力磁吸兼容车载支架和无线充电器；边缘还有加固边框。一个壳等于支架、镜头保护加磁吸功能三合一，而且这个颜色质感特别高级。喜欢这个风格的话，看看下方的橙色购物车吧。",
    },
    "7694098314353986847": {
        "label": "节日礼物推荐",
        "trans": "我爱秋天，我就是爱它。（口播极短，主体为手作泰迪熊玫瑰花束秋季氛围画面展示）",
    },
}
KEE_META = {
    "7693788000328666398": {
        "label": "场景痛点口播",
        "trans": "如果你的妈妈喜欢边做饭边听音乐，别再随便给她买礼物了，直接给她买这个。这是一款手机一放上去就开始播放音乐的音箱，不需要蓝牙、不需要配对、不需要线。最棒的是，它还附带一个小遥控器，不用拿起手机就能控制。而且听音乐的同时它还会给你的手机充电。所以你等于同时拥有音箱、手机遥控器、无线充电器和手机支架，四合一。说实话，这是那种她每天都会真正用到的礼物。而且不只适合妈妈，爸爸也超合适——他可以把它放在车库里，一边做手工一边听音乐。现在 TikTok Shop 还免邮，去点橙色购物车，趁优惠还在赶紧入手一个。",
    },
    "7693857406165470495": {
        "label": "场景痛点口播",
        "trans": "你知道到了酒店才发现忘带 Apple Watch 充电器的那种慌张吗？而这个东西，就是它，能救你。终于有人想到把 Apple Watch 充电器直接做进充电线中间。把表往上一吸就开始充电，AirPods 也适用。两头都是 USB-C，所以也能给手机充电。支持最高 240W 快充，充电速度飞快。线材粗实耐用还不容易打结，旅行携带超级方便。而且刚刚上架，现在价格划算到离谱。需要的话，链接就在视频下方。",
    },
    "7693859313797958943": {
        "label": "卖点口播强调",
        "trans": "SHOKZ 刚刚推出了全新配色的 OpenDots 2，给大家抢先看。天哪，我已经有一副白色的了，但这次我又给妈妈买了一副。这副耳机不分左右耳，直接从充电盒拿出来随便塞进哪只耳朵都行，它会自动识别左右耳。戴着真的可以一整天，因为实在太舒适了，看看它有多柔韧。续航最长 40 小时。在应用里打开杜比音效，人声更清晰、细节更丰富、低音更深沉。还有预设均衡模式：听有声书就切人声模式，想要更重的低音直接打开。想私密收听时还能打开防漏音模式，减少声音外泄。要是丢了一只，还有查找耳机功能，会播放声音帮你找到，就像玩马可波罗游戏一样。如果你喜欢这个新配色，链接就在下方。",
    },
    "7693859636943932703": {
        "label": "场景痛点口播",
        "trans": "这是一件女生会真正想要、而且每天都会用的圣诞礼物。别再乱买礼物了，给她买这个。这是一款手机一放上去就开始播放音乐的音箱，不需要蓝牙、不需要配对、不需要线。最棒的是？一边听喜欢的音乐，它还一边给手机充电。所以它基本上一身兼音箱、无线充电器和手机支架。说实话，这是那种你每天都会真正用到的礼物。也很适合送爸爸——他可以把它放在车库里，一边捣鼓自己的项目一边听音乐。现在 TikTok Shop 提供免邮。",
    },
    "7694096057654971678": {
        "label": "卖点口播强调",
        "trans": "为什么没人早点告诉我这东西这么便宜？如果你不知道这是什么，我快速讲一下。这就是现在人人都在讨论的磁吸便携充电宝。不需要线，往手机背面一吸，立刻开始充电。它还内置两根充电线，一根 USB-C 一根 Lightning，不用再额外带线。iPhone 和安卓都适用，更棒的是还支持快充。无论怎么晃动手机，它都牢牢吸附不掉。它甚至还有内置支架，充电时可以把手机支起来继续看视频。相信我，现在是入手的最佳时机，TikTok Shop 限时特惠还免邮，看到橙色购物车就赶紧下单，优惠结束就没啦。",
    },
}

COLS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P"]
STAT_KEYS = ["playCount", "diggCount", "commentCount", "shareCount", "collectCount"]


def norm_num(v):
    if v is None:
        return 0
    return int(str(v)) if isinstance(v, str) else int(v)


def main():
    ledger_state = json.loads((REPORTS / "ledger_state.json").read_text(encoding="utf-8"))
    transcripts = json.loads((REPORTS / "new_transcripts_clean.json").read_text(encoding="utf-8"))
    out_dir = REPORTS / "write_batches"
    out_dir.mkdir(exist_ok=True)
    batches = []
    plan_summary = {"vivi_ashop": {}, "kee.yaden": {}}

    for acc, sheet, meta, start_row in (
        ("vivi_ashop", "对标记录-vivi_ashop", VIVI_META, 36),
        ("kee.yaden", "对标记录-kee.yaden", KEE_META, 24),
    ):
        payload = json.loads((REPORTS / f"ledger_update_{acc}.json").read_text(encoding="utf-8"))
        e_map = ledger_state["e_maps"][sheet]

        # --- add rows（按发布时间排序）---
        adds = sorted(payload["add_rows"], key=lambda r: r["publish_time_gmt8"])
        add_cells = {}
        for i, row in enumerate(adds):
            r = start_row + i
            vid = row["video_id"]
            if vid in e_map:
                raise SystemExit(f"FATAL: {vid} 已存在于台账 {sheet} 行{e_map[vid]}，重复追加！")
            tr = transcripts[f"{acc}|{vid}"]["transcript"]
            vals = [
                DATE, acc, row["publish_time_gmt8"], row["desc"], row["url"],
                row["products"][0]["link"], row["products"][0]["title"], "",
                norm_num(row["stats"]["playCount"]), norm_num(row["stats"]["diggCount"]),
                norm_num(row["stats"]["commentCount"]), norm_num(row["stats"]["shareCount"]),
                norm_num(row["stats"]["collectCount"]),
                tr, meta[vid]["trans"], meta[vid]["label"],
            ]
            for c, v in zip(COLS, vals):
                add_cells[f"{c}{r}"] = {"value": v}
        # add 批次按 2 行（32 格）拆
        cells_list = list(add_cells.items())
        for bi in range(0, len(cells_list), 32):
            batches.append({"sheet": sheet, "cells": dict(cells_list[bi:bi + 32]),
                            "desc": f"{acc} add {bi//32 + 1}"})
        plan_summary[acc]["add_rows"] = len(adds)
        plan_summary[acc]["add_range"] = f"A{start_row}:P{start_row + len(adds) - 1}"

        # --- update rows（I-M 五列）---
        upd_cells = {}
        miss = []
        for row in payload["update_rows"]:
            vid = row["video_id"]
            if vid not in e_map:
                miss.append(vid)
                continue
            r = e_map[vid]
            for c, k in zip(["I", "J", "K", "L", "M"], STAT_KEYS):
                upd_cells[f"{c}{r}"] = {"value": norm_num(row["stats"][k])}
        if miss:
            raise SystemExit(f"FATAL: {acc} update 行未命中台账: {miss}")
        cells_list = list(upd_cells.items())
        for bi in range(0, len(cells_list), 60):
            batches.append({"sheet": sheet, "cells": dict(cells_list[bi:bi + 60]),
                            "desc": f"{acc} upd {bi//60 + 1}"})
        plan_summary[acc]["update_rows"] = len(payload["update_rows"])
        plan_summary[acc]["update_cells"] = len(upd_cells)

    for i, b in enumerate(batches, 1):
        (out_dir / f"batch_{i:02d}.json").write_text(
            json.dumps({"sheetName": b["sheet"], "cells": b["cells"],
                        "allow_overwrite": True,
                        "explanation": b["desc"]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "plan_summary.json").write_text(
        json.dumps(plan_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(b["cells"]) for b in batches)
    print(json.dumps(plan_summary, ensure_ascii=False, indent=2))
    print(f"batches={len(batches)} total_cells={total}")


if __name__ == "__main__":
    main()
