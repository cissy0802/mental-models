#!/usr/bin/env python3
"""Daily mental models generator — Opus 4.6 for content, runs via GitHub Actions."""

import anthropic
import datetime
import os
import re
import subprocess
import sys

TOPICS = {
    1:  ("决策类",       "decision-making",          ["第一性原理", "二阶思维", "逆向思维", "奥卡姆剃刀"]),
    2:  ("认知偏差类",   "cognitive-biases",         ["确认偏误", "锚定效应", "幸存者偏差", "沉没成本谬误"]),
    3:  ("系统思维类",   "systems-thinking",         ["反馈循环", "杠杆点", "瓶颈理论", "涌现"]),
    4:  ("战略类",       "strategy",                 ["博弈论", "竞争优势", "护城河", "飞轮效应"]),
    5:  ("沟通与影响力类","communication",            ["金字塔原理", "影响力六原则", "框架效应", "故事思维"]),
    6:  ("创新与学习类", "innovation-learning",       ["刻意练习", "T型人才", "跨界迁移", "类比思维"]),
    7:  ("风险与概率类", "risk-probability",          ["贝叶斯思维", "黑天鹅", "反脆弱", "期望值思维"]),
    8:  ("效率与执行类", "efficiency-execution",      ["帕累托法则", "帕金森定律", "时间块理论", "MVP思维"]),
    9:  ("心理与行为类", "psychology-behavior",       ["损失厌恶", "峰终定律", "心流理论", "习惯回路"]),
    10: ("商业模式类",   "business-models",           ["网络效应", "平台思维", "长尾理论", "边际成本"]),
    11: ("哲学与元认知类","philosophy-metacognition",  ["苏格拉底式提问", "知识之筏", "无知之知", "认知谦逊"]),
    12: ("科学方法类",   "scientific-method",         ["假说-验证", "可证伪性", "控制变量", "相关≠因果"]),
    13: ("经济学思维类", "economics",                 ["机会成本", "比较优势", "供需均衡", "外部性"]),
    14: ("投资与财富类", "investment-wealth",          ["安全边际", "均值回归", "复利效应", "资产配置"]),
    15: ("领导力类",     "leadership",                ["仆人式领导", "情境领导", "能力圈", "授权与信任"]),
    16: ("谈判类",       "negotiation",               ["BATNA", "锚定与让步", "双赢思维", "利益vs立场"]),
    17: ("数学思维类",   "mathematical-thinking",     ["大数定律", "回归均值", "幂律分布", "非线性思维"]),
    18: ("生态与进化类", "ecology-evolution",          ["适者生存", "红皇后效应", "共生", "生态位"]),
    19: ("产品思维类",   "product-thinking",           ["用户旅程", "产品-市场契合", "奥卡姆设计", "迭代思维"]),
    20: ("时间与复杂性类","time-complexity",           ["林迪效应", "蝴蝶效应", "路径依赖", "复杂适应系统"]),
    21: ("意识与觉知类", "consciousness-awareness",    ["正念觉察", "元认知", "唯识学", "观察者效应"]),
    22: ("人际关系类",   "relationships",              ["情感账户", "非暴力沟通", "同理心地图", "信任方程式"]),
    23: ("信息与知识管理类","information-knowledge",   ["信噪比", "费曼学习法", "知识图谱", "遗忘曲线"]),
    24: ("伦理与价值类", "ethics-values",              ["电车问题", "功利主义vs义务论", "无知之幕", "道德直觉"]),
    25: ("AI与超级个体类","ai-super-individual",       ["人机协同", "提示工程思维", "AI增强认知", "算力杠杆"]),
    26: ("跨学科迁移类", "interdisciplinary",          ["物理学隐喻", "生物学类比", "历史韵律", "数学建模"]),
    27: ("能量与注意力管理类","energy-attention",      ["注意力残留", "决策疲劳", "能量管理", "深度工作"]),
    28: ("博弈与合作类", "game-cooperation",           ["囚徒困境", "纳什均衡", "重复博弈", "以牙还牙策略"]),
    29: ("认知升级类",   "cognitive-upgrade",          ["心智模型更新", "范式转移", "认知失调", "成长型心态"]),
    30: ("东方智慧类",   "eastern-wisdom",             ["道法自然", "中庸之道", "知行合一", "空性与缘起"]),
}

USER_BACKGROUND = """
用户叫 BigCat，以下背景信息用于生成贴近用户的例子：
- 深度关注 AI 与人机协同，追求成为"AI 超级个体"（用 AI 杠杆放大个人产出）
- 对第一性原理、AI/意识/神经科学/心理学/科学/佛学/量子力学/生物进化学/mental model 等领域有深入兴趣
- 有领导力、教育、投资和商业思维的兴趣
- 重视跨学科思维
- 是一位妈妈，有学龄儿童
- 偏好高效、精确的做事方式
- 沟通风格：正式、精练
"""


def get_day_index(repo_dir: str) -> tuple[int, str]:
    today = datetime.date.today()
    date_str = today.strftime("%Y-%m-%d")
    # Collect all existing day numbers
    existing_days = set()
    pattern = re.compile(r'-day(\d+)\.html$')
    for fname in os.listdir(repo_dir):
        m = pattern.search(fname)
        if m:
            existing_days.add(int(m.group(1)))
    # Find the first missing day in sequence (1, 2, 3, ...)
    day_index = 1
    while day_index in existing_days:
        day_index += 1
    return day_index, date_str


def generate_html(client: anthropic.Anthropic, day_index: int, date_str: str,
                  topic_name: str, slug: str, models: list[str]) -> str:
    prompt = f"""你是一位每日思维模型教练。请为以下主题生成一个完整的、精美的 HTML 页面。

{USER_BACKGROUND}

今天的任务：
- 日期：{date_str}
- 主题：{topic_name}
- 思维模型：{', '.join(models)}

HTML 要求：
- 完整的自包含 HTML（内联 CSS，无外部依赖）
- 风格：现代简洁，浅色背景（#f7f7f5），卡片布局，移动端响应式
- 4 张卡片，border-left 颜色依次为：#6c5ce7, #00b894, #fdcb6e, #e17055
- 每张卡片包含：
  * 模型名称（中文）+ 英文副标题（含一句画龙点睛的破折号短语）
  * 【中文详解】：3 段，涵盖机制原理、非平凡洞见、实践方法；末尾有两个示例：①最经典的通用例子（帮助任何人理解该模型），②一个贴近 BigCat 兴趣的例子（AI/神经科学/佛学/教育/投资/领导力等）；最后有 .scenario 块（场景·BigCat）
  * 【English Summary】：3-5 句精炼英文
  * 【AI Prompts】：一个中文模板 + 一个英文模板（用[方括号]标注填空项）
- 页面顶部：标题"思维模型详解：{topic_name}" + "{date_str} · Day {day_index}"
- 页面底部：返回 index.html 的链接
- section-divider 样式：小型大写字母彩色标签 + 水平分割线
- prompt 代码块：深色背景（#1e272e），等宽字体，黄色小标签

只输出完整 HTML，不要任何解释或 markdown 包裹。"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def build_index(entries: list[tuple[int, str, str, list[str]]]) -> str:
    """Build index.html from list of (day_number, topic_name, filename, models) tuples.
    Entries are listed in ascending day order (new days at the bottom)."""
    rows = ""
    for day_num, topic_name, filename, models in entries:
        model_str = " · ".join(models)
        rows += f"""  <a class="entry" href="{filename}">
    <span class="day">Day {day_num:02d}</span>
    <span class="title">{topic_name}</span>
    <span class="models">{model_str}</span>
  </a>\n"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日思维模型 — Mental Models Daily</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"Noto Sans SC","Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:#f7f8fa;color:#2d3436;line-height:1.8}}
.container{{max-width:720px;margin:0 auto;padding:24px 20px 60px}}
header{{text-align:center;padding:56px 0 40px}}
header h1{{font-size:2rem;font-weight:800;color:#1a1a2e;letter-spacing:2px}}
header p{{margin-top:8px;font-size:1rem;color:#636e72}}
.list{{margin-top:32px}}
.entry{{display:flex;align-items:baseline;gap:16px;padding:18px 20px;background:#fff;border-radius:10px;box-shadow:0 1px 8px rgba(0,0,0,0.05);margin-bottom:14px;transition:transform 0.15s,box-shadow 0.15s;text-decoration:none;color:inherit}}
.entry:hover{{transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,0.1)}}
.entry .day{{flex-shrink:0;font-size:0.85rem;font-weight:700;color:#6c5ce7;font-variant-numeric:tabular-nums;min-width:56px}}
.entry .title{{font-size:1.05rem;font-weight:600;color:#1a1a2e}}
.entry .models{{font-size:0.88rem;color:#636e72;margin-left:auto;text-align:right}}
footer{{text-align:center;padding:40px 0 12px;font-size:0.82rem;color:#b2bec3}}
@media(max-width:600px){{
  header h1{{font-size:1.5rem}}
  .entry{{flex-direction:column;gap:4px}}
  .entry .models{{margin-left:0;text-align:left}}
}}
</style>
</head>
<body>
<div class="container">
<header>
  <h1>每日思维模型</h1>
  <p>Mental Models Daily — BigCat's Thinking Toolkit</p>
</header>
<div class="list">
{rows}</div>
<footer>Powered by curiosity &amp; first principles.</footer>
</div>
</body>
</html>"""


def scan_existing_entries(repo_dir: str) -> list[tuple[int, str, str, list[str]]]:
    """Scan repo for existing HTML files and extract metadata from filenames.
    Returns list of (day_number, topic_name, filename, models) sorted by day ascending."""
    entries = []
    pattern = re.compile(r'^(.+)-day(\d+)\.html$')
    for fname in os.listdir(repo_dir):
        if fname == "index.html":
            continue
        m = pattern.match(fname)
        if not m:
            continue
        slug, day_num = m.group(1), int(m.group(2))
        # Find matching topic
        topic_name = slug
        models = []
        for _, (tname, _tslug, tmodels) in TOPICS.items():
            if _tslug == slug:
                topic_name = tname
                models = tmodels
                break
        entries.append((day_num, topic_name, fname, models))
    entries.sort(key=lambda e: e[0])  # ascending by day number
    return entries


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    repo_dir = os.path.dirname(os.path.abspath(__file__))

    day_index, date_str = get_day_index(repo_dir)

    if day_index in TOPICS:
        topic_name, slug, models = TOPICS[day_index]
    else:
        # Day 31+: generate a new topic via Opus
        existing = [v[0] for v in TOPICS.values()]
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content":
                f"已有主题：{existing}。请提出一个全新的思维模型主题（不重复），"
                "并给出4个该主题下的思维模型名称。"
                "输出格式（仅输出这一行）：主题名|slug|模型1,模型2,模型3,模型4"}],
        )
        line = resp.content[0].text.strip()
        parts = line.split("|")
        topic_name = parts[0]
        slug = parts[1] if len(parts) > 1 else "custom"
        models = parts[2].split(",") if len(parts) > 2 else ["模型A", "模型B", "模型C", "模型D"]

    filename = f"{slug}-day{day_index:02d}.html"
    filepath = os.path.join(repo_dir, filename)

    if os.path.exists(filepath):
        print(f"Skipping: {filename} already exists.")
        return

    print(f"Generating: {topic_name} → {filename}")
    html = generate_html(client, day_index, date_str, topic_name, slug, models)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Written: {filepath}")

    # Rebuild index with all entries (ascending day order, new days at bottom)
    entries = scan_existing_entries(repo_dir)
    # Ensure today's entry is in the list
    if not any(e[2] == filename for e in entries):
        entries.append((day_index, topic_name, filename, models))
        entries.sort(key=lambda e: e[0])

    index_path = os.path.join(repo_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(build_index(entries))
    print("Updated index.html")

    # Git commit
    subprocess.run(["git", "config", "user.email", "chengchen0802@gmail.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "BigCat"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", filename, "index.html"], cwd=repo_dir, check=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir)
    if result.returncode != 0:
        models_str = "、".join(models)
        subprocess.run(
            ["git", "commit", "-m", f"Add Day {day_index}: {topic_name} ({models_str})"],
            cwd=repo_dir, check=True
        )
        subprocess.run(["git", "push", "origin", "main"], cwd=repo_dir, check=True)
        print("Pushed to GitHub.")
    else:
        print("Nothing to commit.")


if __name__ == "__main__":
    main()
