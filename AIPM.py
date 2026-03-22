import html as html_module
import json
import os
import re
import ssl
import urllib.error
import urllib.request

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from openai import OpenAI

# 1. 页面基础配置
st.set_page_config(
    page_title="街灯 AI 制片工作台",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 企划书尚未生成时的占位文案（用于判断第三列是否展示推荐班底）
PROPOSAL_PLACEHOLDER_TEXT = "【街灯 AI 制片助理正在与您沟通，项目企划书将在此处实时生成…】"
AI_FACE_NAME = "街灯 AI 制片助理"


def is_proposal_placeholder(p: str) -> bool:
    t = (p or "").strip()
    return bool(t.startswith("【") and "正在与您沟通" in t and "项目企划书将在此处" in t)

# 2. API Key：优先环境变量与 secrets，硬编码仅作本地演示兜底（勿提交真实 Key 到公开仓库）


def _resolve_api_key() -> str:
    v = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if v:
        return v
    try:
        if hasattr(st, "secrets") and "DEEPSEEK_API_KEY" in st.secrets:
            return str(st.secrets["DEEPSEEK_API_KEY"]).strip()
    except (FileNotFoundError, KeyError, TypeError):
        pass
    if API_KEY_HARDCODED.strip():
        return API_KEY_HARDCODED.strip()
    return ""


API_KEY = _resolve_api_key()
if not API_KEY:
    st.error(
        "请配置 DeepSeek API Key：**推荐**设置环境变量 `DEEPSEEK_API_KEY` 或 `.streamlit/secrets.toml` 中的 `DEEPSEEK_API_KEY`；"
        "仅本地演示可在代码里填写 `API_KEY_HARDCODED`（勿提交公开仓库）。"
    )
    st.stop()

client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")

# 左侧对话 iframe 高度（内容在框内滚动）
CHAT_IFRAME_HEIGHT = 280

# 3. 十步信息收集：每步对应一组「Cursor 式」快捷回复（可再配合自由输入）
INTAKE_STAGES = [
    {
        "topic": "项目类型",
        "options": ["音乐 MV", "短视频 / 口播", "形象片 / 广告片", "活动纪录 / 花絮", "还不确定，先帮我捋一捋"],
    },
    {
        "topic": "主要在哪拍",
        "options": ["成都实景（市区 / 犀浦一带）", "棚内置景", "外地拍摄", "门店 / 办公室实拍", "地点还没定"],
    },
    {
        "topic": "整体预算区间",
        "options": ["三万以内", "大概三到八万", "八万到二十万", "二十万以上", "预算看方案再定"],
    },
    {
        "topic": "视觉 / 叙事风格",
        "options": ["纪实感 / 生活流", "电影感 / 强光影", "赛博 / 潮流视觉", "清新日系", "风格听你们专业建议"],
    },
    {
        "topic": "更具体的地点或场景",
        "options": ["犀浦或郫都周边", "成都市区地标", "无影棚纯色底", "多场景混拍", "具体地址我后面发你"],
    },
    {
        "topic": "成片时长",
        "options": ["1 分钟内", "1–3 分钟", "3–5 分钟", "5 分钟以上", "时长未定"],
    },
    {
        "topic": "声音需求（配乐 / 人声 / 音效）",
        "options": ["要原创或版权配乐", "客户自带音乐", "要旁白 / 口播录制", "现场收音为主 + 简单后期", "声音部分你们帮我定"],
    },
    {
        "topic": "出镜与表演",
        "options": ["需要专业演员", "素人 / 员工出镜即可", "无真人出镜", "需要群演", "还不确定"],
    },
    {
        "topic": "交付规格",
        "options": ["竖屏短视频平台", "横屏 16:9 主片", "多版本（横竖都要）", "只要粗剪小样", "规格听你们建议"],
    },
    {
        "topic": "希望交片时间",
        "options": ["越快越好（加急）", "两周内", "一个月内", "两到三个月", "日期灵活"],
    },
]

PROPOSAL_KEYWORDS = (
    "生成最终的项目企划书",
    "生成企划书",
    "可以了",
    "我们做企划书",
    "出企划书",
    "总结并生成",
)

# 模型在「信息已够」时于回复末尾输出此行（仅此一行）；用户侧会剥离并自动触发生成企划书
AUTO_PROPOSAL_SIGNAL = "[[GENERATE_PROPOSAL_NOW]]"


def assistant_reply_triggers_proposal(text: str) -> bool:
    """助手自然语言里表达「可以出企划/可以生成」时触发；排除明显否定或反问用户。"""
    t = (text or "").strip()
    if not t:
        return False
    # 反问用户时勿自动代劳
    if re.search(
        r"(你可以|您要|要不要|需不需要|可以吗|行不行|要我现在|要我这边).{0,12}(生成|出企划|出方案|做大纲)",
        t,
    ):
        return False
    # 明确否定「能生成」
    if "不可以生成" in t or "还不能生成" in t or "暂时不能生成" in t or "先别生成" in t:
        return False
    # 用户要的核心：出现「可以生成」即视为可出稿（上面已挡「不可以/还不能…生成」）
    if "可以生成" in t:
        return True
    if re.search(r"可以.{0,5}(帮您|给你|为你).{0,4}生成", t):
        return True
    extra = (
        "可以出企划书",
        "可以出企划了",
        "能出企划书",
        "能生成企划",
        "可以出大纲",
        "可以出方案",
        "可以做方案",
        "开始做方案",
        "开始做计划",
        "开始弄方案",
        "先输出方案",
        "先出企划",
        "给你出企划",
        "给你出大纲",
        "这就出企划",
        "出正式企划",
        "出建组大纲",
        "总结并生成",
        "生成企划书",
        "马上出大纲",
    )
    if any(p in t for p in extra):
        return True
    if re.search(r"没问题.{0,8}开始做", t):
        return True
    return False


def split_auto_proposal_signal(raw: str) -> tuple[str, bool]:
    """返回 (给用户看的正文, 是否应自动生成企划书)。含隐藏标记或助手口头「可以生成」类表述均会触发。"""
    t = raw or ""
    has_marker = AUTO_PROPOSAL_SIGNAL in t
    cleaned = t.replace(AUTO_PROPOSAL_SIGNAL, "").strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if has_marker and not cleaned:
        cleaned = "好，关键信息齐了，我这就给你出正式企划与建组大纲。"
    should = has_marker or assistant_reply_triggers_proposal(t)
    return cleaned, should

SYSTEM_BASE = """你是【街灯】平台的 AI 制片助理（对甲方可称：街灯 AI 制片 / AI 项目经理）。接待外行甲方，用自然语言把需求问清楚。
语气：资深制片 / 项目经理，专业、接地气，像真人微信沟通，每次只问 1–2 个小问题，不要长篇大论。

**提问方式**：不要按固定脚本顺序机械推进。你要自己读完整对话，判断「清单里还有哪些维度用户还没说清楚或不够具体」，就优先问缺的；若用户一条里已经带了好几项信息，要接住并追问尚未覆盖的维度。
已说清楚的信息不要重复追问。技术细节（灯光档位、器材型号等）可在企划书里推断，不必事事当场问死。

成都行情（犀浦、棚内、实景等）下做粗估即可，注明「仅供参考，以实际报价为准」。"""

INFO_DIMENSIONS_BLOCK = """【内部核对清单 · 最终尽量要覆盖的维度】（仅供你判断「还缺什么」，对用户提问要自然口语化，不要背诵清单）
- 项目类型（MV / 短视频 / 广告片等）
- 主要拍摄方式与大致地点（实景 / 棚内 / 外地等）
- 整体预算区间
- 视觉与叙事风格
- 更具体的场景或地标（若项目需要）
- 成片时长或篇幅感
- 声音：配乐 / 人声 / 配音 / 音效
- 出镜与表演（演员 / 素人 / 无真人等）
- 交付规格（横竖屏、平台、版本）
- 希望交片时间

当你判断以上维度**已够写出正式《企划与建组大纲》**时：
- **不要**再让用户「点按钮」「确认一遍要不要生成」；不要引导对方说「可以了」类口令。
- 可直接在回复里用口语表示可以出稿，例如：**「可以生成（企划/大纲）了」「可以出企划书了」「没问题，开始做方案」**等——**系统会自动识别并立刻生成**，用户无需再操作。
- 若仍习惯用机器标记：也可**单独起一行**只写 `[[GENERATE_PROPOSAL_NOW]]`（勿加反引号、勿用代码块；不要向用户解释该标记）；用户界面上不显示此行。"""


def build_system_content() -> str:
    return SYSTEM_BASE + "\n\n" + INFO_DIMENSIONS_BLOCK


def is_proposal_request(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    return any(k in t for k in PROPOSAL_KEYWORDS)


def _extract_json_dict_from_text(raw: str) -> dict | None:
    text = (raw or "").strip()
    if "```" in text:
        for ch in text.split("```"):
            ch = ch.strip()
            if ch.lower().startswith("json"):
                ch = ch[4:].lstrip()
            if ch.startswith("{"):
                text = ch
                break
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        obj = json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def synthesize_quick_options(client: OpenAI, messages: list) -> tuple[str, list[str]]:
    """根据助手最新一轮回复，生成与当前追问强相关的快捷按钮（额外一次小模型调用）。"""
    last_asst = ""
    for m in reversed(messages):
        if m["role"] == "assistant":
            last_asst = m["content"]
            break
    if not last_asst:
        return INTAKE_STAGES[0]["topic"], list(INTAKE_STAGES[0]["options"])

    tail = last_asst.strip()[-4000:]
    sys = """你是制片沟通「快捷按钮」文案撰写。只输出一个 JSON 对象，不要 markdown、不要其它文字。

输入：AI 制片助理对用户说的**最后一整段话**。
任务：找出助理**当前主要在等用户回答的那一个核心问题**（若连着多问，取最后、最具体的那一问）。针对这一问，写 5 条**像真人 Boss 会点的回复**——读起来完整、自然、好懂，不要干巴巴的关键词堆砌。

硬性要求：
- 每条 8～22 个字为宜，口语化，像微信里随手回一句；不要「1.」编号，不要书名号套模板
- 必须与**当前这一问**强相关：问配音就给配音相关说法，问时长就给时长，问地点就给地点——**禁止**用无关维度凑数
- 其中一条可以是委婉兜底，例如「这块你们专业看着办」或「我后面再细说」

输出格式严格为：
{"topic":"对当前追问的2～10字概括","options":["...","...","...","...","..."]}"""

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": "助手最新回复如下：\n\n" + tail},
            ],
            temperature=0.25,
            max_tokens=450,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception:
        return "", []

    obj = _extract_json_dict_from_text(raw)
    if not obj:
        return "", []
    topic = str(obj.get("topic") or "").strip()[:24] or "当前问题"
    opts_raw = obj.get("options")
    if not isinstance(opts_raw, list):
        return topic, []
    clean = []
    for o in opts_raw[:6]:
        s = str(o).strip()
        if s and len(s) <= 28:
            clean.append(s)
    if len(clean) < 3:
        return "", []
    return topic, clean[:6]


def fetch_url_page_title(url: str, timeout: float = 12.0) -> tuple[str, str]:
    """尽力抓取网页 <title>（B 站等常为服务端 HTML，可能失败）。返回 (title, error_msg)。"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            method="GET",
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            chunk = r.read(600_000)
        raw = chunk.decode("utf-8", errors="ignore")
        m = re.search(r"<title[^>]*>([^<]+)</title>", raw, re.I | re.DOTALL)
        title = html_module.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""
        if not title:
            m2 = re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
                raw,
                re.I,
            )
            if m2:
                title = html_module.unescape(m2.group(1).strip())
        return title, ""
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return "", str(e)[:240]
    except Exception as e:
        return "", str(e)[:240]


LINK_DEMO_NO_METADATA = (
    "【演示】链接已记下，页面标题、时长等信息这边读不到（演示里很常见：防爬虫或页面动态加载）。"
    "不用凑假片名——你直接跟我说想做成什么感觉、给谁看就行。"
)


def analyze_reference_url_style(client: OpenAI, url: str) -> str:
    """无法看视频。抓不到标题时不调用模型、不写编造信息，仅返回简短演示说明。"""
    title, err = fetch_url_page_title(url)
    if not (title or "").strip():
        return LINK_DEMO_NO_METADATA

    sys = """你是影视制片方向的顾问。用户会提供一条**视频详情页链接**（可能是 B 站等）。
重要限制：你**不能观看视频画面**，也可能拿不到完整简介；只能根据 URL、域名、以及若提供的**网页标题**做**有限推测**。

请用中文写一段 **220～400 字**的说明（不要用 markdown 标题符号 #），结构包含：
1) 简要说明「未看原片、以下仅为根据标题/域名的推测」
2) 推测内容形态、受众、节奏与画面气质（可从标题用词联想；不确定就明确写不确定）
3) 给出 **4～6 个**适合写进需求表的**短标签**（用中文顿号「、」连接在一行里，例如：竖屏、快节奏、口播、字幕花字多）
4) 提醒用户补充：成片参考、对标账号、或再贴一段文字描述会更准

语气专业、短句为主。"""
    user = f"链接：{url}\n抓取到的页面标题：{title}\n抓取异常说明：{err or '无'}"
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            max_tokens=700,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"（分析请求失败：{e}）请直接描述你想要的风格，或换个链接再试。"


def apply_quick_options_to_session(fallback_step: int, n_stages: int, done_core: bool):
    """把 synthesize 结果写入 session；失败则回退到静态 INTAKE 或自由补充。"""
    topic, opts = synthesize_quick_options(client, st.session_state.messages)
    if opts and len(opts) >= 3:
        st.session_state["dynamic_topic_label"] = topic
        st.session_state["dynamic_quick_options"] = opts
    elif done_core:
        st.session_state["dynamic_topic_label"] = "自由补充"
        st.session_state["dynamic_quick_options"] = [
            "还想补充：预算想再压一点",
            "拍摄想再加一个场景",
            "交片日期有变，再说一下",
            "没有别的了，出企划书吧",
        ]
    else:
        step = min(max(fallback_step, 0), n_stages - 1)
        st.session_state["dynamic_topic_label"] = INTAKE_STAGES[step]["topic"]
        st.session_state["dynamic_quick_options"] = list(INTAKE_STAGES[step]["options"])
    st.session_state["quick_options_rev"] = st.session_state.get("quick_options_rev", 0) + 1


def proposal_user_instruction() -> str:
    return """请根据以上聊天记录提取关键信息，直接输出一份 Markdown《街灯项目企划与建组大纲》。不要客套废话，不要重复聊天内容，直接输出文档。

文档必须包含以下区块（标题可用你略作调整，但信息要有）：

1. **项目概述**：类型、风格、时长、交片期望一句话摘要。
2. **关键信息表**：拍摄地、预算区间、声音与出镜需求、交付规格等（Markdown 表格）。
3. **岗位与外包建议表**（核心）：列 | 岗位 | 是否必需 | 建议自建/外包 | 预估费用(元，区间即可) | 匹配度评分(1-10) | 说明/周期 |
   至少覆盖：导演、制片、摄影、灯光（若推断需要）、收音/同期、美术或场务、剪辑、调色、配乐/声音后期等；没有的岗位可写「本轮不涉及」。
4. **设备与灯光推断**：根据项目类型与风格说明为何需要或简化灯光、大致设备档位；可提及**租赁**大致档位（成都/犀浦周边行情粗估即可，注明非官方价目）。
5. **时间计划（里程碑）**：用表格列 | 阶段 | 主要产出 | 建议完成日期（可写相对周数或具体「X月X日前」示例，基于用户交片期望倒推）|。
6. **外包与风险**：哪些环节适合外包、1–3 条风险与规避建议。

全文使用 Markdown 表格与分级标题，便于人类 PM 在右侧直接改。"""


def run_proposal_generation(activity_slot, n_stages: int) -> None:
    """根据当前对话生成企划书、更新中列/右列，并追加一条助手说明。"""
    activity_slot.info("正在生成企划书，请稍候…")
    doc_messages = st.session_state.messages.copy()
    doc_messages[0] = {
        "role": "system",
        "content": SYSTEM_BASE
        + "\n\n你现在要输出正式企划文档，语气保持专业简练，按用户要求的 Markdown 结构输出。",
    }
    doc_messages.append({"role": "user", "content": proposal_user_instruction()})
    try:
        stream = client.chat.completions.create(
            model="deepseek-chat",
            messages=doc_messages,
            stream=True,
        )
        full_doc = ""
        for chunk in stream:
            if chunk.choices[0].delta.content:
                full_doc += chunk.choices[0].delta.content
    except Exception as e:
        activity_slot.error(f"企划书生成失败：{e}")
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": f"（企划书生成失败：{e}）请检查网络与 DeepSeek API Key 后重试。",
            }
        )
        return
    st.session_state.proposal = full_doc
    st.session_state.proposal_rev += 1
    activity_slot.success(
        "企划书已生成：中列为排版预览；右列为人员推荐、场地/设备/餐饮等分项与总预算粗估（演示）。"
    )
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": "企划书已生成：中间列为文档预览；最右列为推荐人员、场地与其它成本表及总预算粗估（演示）。可在中列展开编辑源码微调。",
        }
    )
    try:
        with st.spinner("正在生成第三列成本与班底表…"):
            finalize_cost_sheet_after_proposal(full_doc)
        with st.spinner("正在生成与当前话题对齐的快捷选项…"):
            apply_quick_options_to_session(
                st.session_state.intake_step, n_stages, st.session_state.intake_step >= n_stages
            )
    except Exception as e:
        st.warning(f"企划书已写入，但成本表或快捷按钮生成异常：{e}")


def generate_cost_sheet_json(client: OpenAI, proposal_md: str) -> dict | None:
    """根据企划书生成第三列用的结构化成本+推荐人（无本地价目表，模型粗估）。"""
    clip = (proposal_md or "")[:14000]
    sys = """你是成都/犀浦及周边影视项目的成本**粗算**助手。平台**没有**官方价目表，请给**区间/量级**即可，并明确这是推测。

只输出 **一个 JSON 对象**（不要 markdown 围栏、不要其它文字），字段如下：

1) "people"：数组。每项必须含：
   - "role"：岗位（如 导演、制片、摄影指导、灯光师、收音/同期、剪辑、调色、声音后期 等，按项目需要列，不需要的岗不要硬凑）
   - "name"：**虚构**中文姓名（演示用）
   - "price_note"：参考报价短语（如「约 8k–1.2 万/天」或「打包 1.5–3 万/条」）
   - "blurb"：一句话擅长（≤30 字）
   - "projects"：虚构的极简履历（≤25 字）

2) "other_costs"：数组。每项含 "item"、"estimate"、"note"（note 可空字符串）。
   **尽量覆盖**这些类目（若项目明显不需要，estimate 可写「本轮可忽略」或「—」）：
   - 摄影机+镜头等**设备租赁**
   - **灯光**设备及附件租赁/电费
   - **棚租**或外景场地费
   - **影视基地**场地/管理费（若适用）
   - 摄制组**餐饮**
   - **车辆/货运/货拉拉**等运输
   - **零食饮料、杂费**耗材
   - 道具美术等**其它**（若适用）

3) "total_range"：字符串，如「约 8–15 万（全片粗估）」

4) "disclaimer"：固定写「演示粗估，非成都官方价目，以实际报价为准」"""

    user = "以下为《企划书》Markdown，请据此输出 JSON：\n\n" + clip
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
            temperature=0.35,
            max_tokens=2500,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception:
        return None
    return _extract_json_dict_from_text(raw)


def _normalize_cost_sheet(obj: dict | None) -> dict:
    """校验并补默认结构。"""
    if not isinstance(obj, dict):
        return {}
    people = obj.get("people")
    if not isinstance(people, list):
        people = []
    clean_p = []
    for p in people[:12]:
        if not isinstance(p, dict):
            continue
        clean_p.append(
            {
                "role": str(p.get("role") or "岗位").strip()[:20],
                "name": str(p.get("name") or "待定").strip()[:12],
                "price_note": str(p.get("price_note") or "面议").strip()[:40],
                "blurb": str(p.get("blurb") or "").strip()[:40],
                "projects": str(p.get("projects") or "").strip()[:40],
            }
        )
    other = obj.get("other_costs")
    if not isinstance(other, list):
        other = []
    clean_o = []
    for o in other[:20]:
        if not isinstance(o, dict):
            continue
        clean_o.append(
            {
                "item": str(o.get("item") or "").strip()[:40],
                "estimate": str(o.get("estimate") or "—").strip()[:36],
                "note": str(o.get("note") or "").strip()[:60],
            }
        )
    return {
        "people": clean_p,
        "other_costs": clean_o,
        "total_range": str(obj.get("total_range") or "待核算").strip()[:80],
        "disclaimer": str(obj.get("disclaimer") or "演示粗估，以实际报价为准").strip()[:120],
    }


# 与模型推荐并列的「换人」候选项（演示）
CREW_ALTERNATES_BY_KEYWORD = [
    ("导演", [{"name": "韩北川", "price_note": "1–1.5 万/天", "blurb": "人文与艺人向", "projects": "纪录短片×2、品牌片×4"}]),
    ("制片", [{"name": "顾里", "price_note": "7k–1.1 万/天", "blurb": "控预算、盯场", "projects": "棚拍周片×6"}]),
    ("摄影", [{"name": "江行", "price_note": "9k–1.4 万/天", "blurb": "手持/稳定器", "projects": "夜景 MV×3"}]),
    ("灯光", [{"name": "陆昭", "price_note": "5k–8k/天", "blurb": "小团队布光", "projects": "产品棚拍×5"}]),
    ("剪辑", [{"name": "方回", "price_note": "打包 1–2.5 万/条", "blurb": "快节奏网感", "projects": "短视频系列"}]),
    ("调色", [{"name": "白澈", "price_note": "4k–9k/条", "blurb": "肤色与统一感", "projects": "广告调色×4"}]),
]


def _alternates_for_role(role: str, primary_name: str) -> list[dict]:
    alts: list[dict] = []
    r = role or ""
    for kw, candidates in CREW_ALTERNATES_BY_KEYWORD:
        if kw in r:
            for c in candidates:
                if c["name"] != primary_name:
                    alts.append(dict(c))
            break
    return alts


def build_fallback_cost_sheet() -> dict:
    people = []
    for row in DEMO_CREW_ROWS:
        people.append(
            {
                "role": row["role"],
                "name": row["name"],
                "price_note": row["rate"],
                "blurb": row["skills"][:32],
                "projects": row["projects"][:32],
            }
        )
    other = [
        {"item": "摄影机+镜头租赁（档期满配）", "estimate": "约 2k–8k/天", "note": "视机身与镜头组"},
        {"item": "灯光设备及附件", "estimate": "约 1.5k–6k/天", "note": "含部分电费/耗材"},
        {"item": "棚租 / 实景场地", "estimate": "约 2k–1.5 万/天", "note": "犀浦/市区差异大"},
        {"item": "影视基地场地（如适用）", "estimate": "视基地报价", "note": "门票/管理费另计"},
        {"item": "摄制组餐饮", "estimate": "约 80–150 元/人/天", "note": "按人头与天数粗估"},
        {"item": "车辆 / 货拉拉 / 运输", "estimate": "约 300–2000/趟", "note": "器材与道具搬运"},
        {"item": "零食饮料与现场杂费", "estimate": "约 300–1500/天", "note": "演示量级"},
        {"item": "道具美术杂项（如适用）", "estimate": "单列另议", "note": "—"},
    ]
    return {
        "people": people,
        "other_costs": other,
        "total_range": "约 6–20 万（视片型与天数，演示区间）",
        "disclaimer": "演示粗估，非成都官方价目，以实际报价为准",
    }


def render_cost_sheet_column():
    sheet = st.session_state.get("cost_sheet")
    if not sheet or not isinstance(sheet, dict):
        st.caption("成本表未就绪。")
        return

    if "cost_alt_idx" not in st.session_state:
        st.session_state.cost_alt_idx = {}

    people = sheet.get("people") or []
    st.markdown("##### 推荐人员（演示 · 虚构名）")
    st.caption(
        "以下为根据企划书粗估的**班底与报价量级**，可点「换一换」看同岗位备选，「邀请」为演示占位（非真实发单）。"
    )

    for i, p in enumerate(people):
        primary = {
            "name": p.get("name"),
            "price_note": p.get("price_note"),
            "blurb": p.get("blurb"),
            "projects": p.get("projects"),
        }
        alts = [primary] + _alternates_for_role(p.get("role", ""), primary["name"])
        # 去重 name
        seen = set()
        uniq = []
        for a in alts:
            if a["name"] in seen:
                continue
            seen.add(a["name"])
            uniq.append(a)
        alts = uniq if uniq else [primary]

        idx = int(st.session_state.cost_alt_idx.get(str(i), 0)) % len(alts)
        cur = alts[idx]
        inv_key = f"{p.get('role')}|{cur['name']}"

        with st.container(border=True):
            h1, h2, h3, h4 = st.columns([1.0, 0.95, 1.15, 1.25])
            with h1:
                st.markdown(f"**{p.get('role')}**")
            with h2:
                st.markdown(f"**{cur['name']}**")
            with h3:
                st.caption(cur.get("price_note") or "")
            with h4:
                b1, b2 = st.columns(2)
                with b1:
                    if len(alts) > 1 and st.button("换一换", key=f"swap_cost_{i}", use_container_width=True):
                        st.session_state.cost_alt_idx[str(i)] = (idx + 1) % len(alts)
                        st.rerun()
                with b2:
                    if inv_key in st.session_state.crew_invited:
                        st.caption("已邀")
                    elif st.button("邀请", key=f"inv_cost_{i}", use_container_width=True):
                        st.session_state.crew_invited.add(inv_key)
                        msg = f"已邀请「{cur['name']}」（{p.get('role')}）· 演示"
                        toast = getattr(st, "toast", None)
                        if callable(toast):
                            toast(msg, icon="📨")
                        else:
                            st.success(msg)
                        st.rerun()
            st.caption(f"{cur.get('blurb') or ''} ｜ {cur.get('projects') or ''}")

    st.divider()
    st.markdown("##### 场地 · 设备 · 其它成本（粗估）")
    others = sheet.get("other_costs") or []
    if others:
        lines = ["| 项目 | 预估 | 说明 |", "| --- | --- | --- |"]
        for o in others:
            it = (o.get("item") or "").replace("|", "/")
            es = (o.get("estimate") or "").replace("|", "/")
            nt = (o.get("note") or "").replace("|", "/")
            lines.append(f"| {it} | {es} | {nt} |")
        st.markdown("\n".join(lines))
    else:
        st.caption("（无分项）")

    st.divider()
    st.markdown("##### 费用总预算（粗估）")
    st.markdown(f"### {sheet.get('total_range') or '—'}")
    st.caption(sheet.get("disclaimer") or "")


# 演示用虚构班底（给投资人看交互雏形，非真实人选）
DEMO_CREW_ROWS = [
    {
        "id": "c1",
        "name": "陈牧野",
        "role": "导演",
        "score": 9.2,
        "rate": "1.2–1.8 万/天",
        "skills": "商业片节奏、棚内置景、演员调度",
        "projects": "车企发布片×3、艺人 MV×2",
    },
    {
        "id": "c2",
        "name": "林澄",
        "role": "制片 / 执行制片",
        "score": 8.8,
        "rate": "8k–1.2 万/天",
        "skills": "预算拆解、场地统筹、成都本地资源",
        "projects": "犀浦实景短剧、棚拍周片×5",
    },
    {
        "id": "c3",
        "name": "赵砚舟",
        "role": "摄影指导",
        "score": 9.0,
        "rate": "1–1.5 万/天",
        "skills": "电影感光影、稳定器/轨道、小团队高效",
        "projects": "赛博风 MV、夜景街拍纪录",
    },
    {
        "id": "c4",
        "name": "唐诗瑶",
        "role": "灯光师",
        "score": 8.5,
        "rate": "6k–9k/天",
        "skills": "LED 虚拟棚、氛围光、产品高光",
        "projects": "棚拍产品、直播场景布光",
    },
    {
        "id": "c5",
        "name": "周予安",
        "role": "剪辑 + 声音精修",
        "score": 8.7,
        "rate": "打包 1.5–3 万/条（视成片）",
        "skills": "节奏剪辑、对白降噪、简单拟音",
        "projects": "短视频系列、口播精剪",
    },
    {
        "id": "c6",
        "name": "沈嘉禾",
        "role": "调色",
        "score": 8.4,
        "rate": "5k–1 万/条",
        "skills": "风格化 LUT、肤色统一、交付多规格",
        "projects": "广告调色、MV 复古/赛博两套 look",
    },
]


# 进行中项目演示数据（投资人「第二幕」· 与真实 API 解耦）
DEMO_ACTIVE_PROJECTS: list[dict] = [
    {
        "id": "demo_mv_xipu",
        "title": "犀浦实景 · 艺人单曲 MV",
        "phase": "拍摄期",
        "next_deadline": "3 月 28 日（计划杀青）",
        "health": "正常",
        "planning_summary": [
            "本周：D2 日戏主场景 + D3 夜景补拍；场务与转场路线已钉死。",
            "风险：周末场地若冲突，已预留 B 场地（合同附录）。",
        ],
        "members": [
            {
                "name": "陈牧野",
                "role": "导演",
                "progress": 0.78,
                "last_done": "昨日 18:42 锁定分镜 v4，同步摄影与灯光",
            },
            {
                "name": "林澄",
                "role": "制片",
                "progress": 0.85,
                "last_done": "今日 10:05 更新 Call Sheet，确认餐饮与车辆",
            },
            {
                "name": "赵砚舟",
                "role": "摄影指导",
                "progress": 0.62,
                "last_done": "昨日 21:10 上传试光静帧至项目盘",
            },
            {
                "name": "唐诗瑶",
                "role": "灯光",
                "progress": 0.55,
                "last_done": "今日 09:20 清点 LED 与附件装车单",
            },
            {
                "name": "周予安",
                "role": "剪辑（驻场粗剪）",
                "progress": 0.25,
                "last_done": "D1 素材已 DIT 备份，粗剪时间线待拍毕补齐",
            },
        ],
        "milestones": [
            {"name": "勘景与试光", "ratio": 1.0, "note": "主场景 + 备用景已确认", "due": "3/18"},
            {"name": "筹备（Cast/服化道）", "ratio": 0.92, "note": "演员档期已锁，服化最后一轮对表", "due": "3/22"},
            {"name": "拍摄", "ratio": 0.45, "note": "D1 完成，D2–D3 待拍", "due": "3/28"},
            {"name": "粗剪 + 客户审片", "ratio": 0.08, "note": "待杀青后 5 工作日内 v1", "due": "4/05"},
            {"name": "精剪 / 调色 / 交付", "ratio": 0.0, "note": "依审片意见排期", "due": "4/20"},
        ],
        "budget": {"total": 158000, "spent": 62400, "frozen": 24000},
        "links": {
            "meeting": "https://meeting.tencent.com/dm/demo-popcrew-mv",
            "group": "https://work.weixin.qq.com/?demo=popcrew-group-mv",
        },
        "timeline_ratios": [1.0, 1.0, 1.0, 0.92, 0.45, 0.28, 0.08, 0.0, 0.0],
        "ledger": [
            {"日期": "3/20", "类型": "场地", "摘要": "外景日租金（主场景）", "金额": "¥12,000", "状态": "已核准"},
            {"日期": "3/21", "类型": "餐饮", "摘要": "D1 剧组盒餐 28 人份", "金额": "¥3,360", "状态": "已报销"},
            {"日期": "3/21", "类型": "设备", "摘要": "灯光车补 + 耗材", "金额": "¥1,850", "状态": "待复核"},
            {"日期": "3/22", "类型": "劳务", "摘要": "摄影组 D1 节点预支", "金额": "¥18,000", "状态": "已放款"},
            {"日期": "3/22", "类型": "对公", "摘要": "场地押金（可退）", "金额": "¥8,000", "状态": "付款单已确认"},
            {"日期": "3/23", "类型": "增项", "摘要": "夜景发电车（口头纪要→待单）", "金额": "¥4,200", "状态": "待立项"},
        ],
    },
    {
        "id": "demo_brand_tvc",
        "title": "科技品牌 · 15s 竖屏广告",
        "phase": "后期",
        "next_deadline": "4 月 2 日（内部审 v2）",
        "health": "关注交片",
        "planning_summary": [
            "本周：调色一版 + 字幕包装定稿；客户周三窗口审片。",
            "风险：LOGO 动效第三方素材授权待邮件回签（已催）。",
        ],
        "members": [
            {
                "name": "沈嘉禾",
                "role": "调色",
                "progress": 0.7,
                "last_done": "今日 14:30 输出 Look 预览 6 镜",
            },
            {
                "name": "周予安",
                "role": "剪辑",
                "progress": 0.88,
                "last_done": "昨日 17:00 交付 v1，待客户批注",
            },
            {
                "name": "林澄",
                "role": "制片",
                "progress": 0.9,
                "last_done": "今日 11:20 同步客户会议纪要与修改边界",
            },
        ],
        "milestones": [
            {"name": "脚本与分镜", "ratio": 1.0, "note": "客户已书面确认", "due": "2/10"},
            {"name": "棚拍", "ratio": 1.0, "note": "素材已归档哈希", "due": "2/25"},
            {"name": "粗剪", "ratio": 1.0, "note": "v1 已发", "due": "3/12"},
            {"name": "精剪 / 包装 / 调色", "ratio": 0.62, "note": "进行中", "due": "4/02"},
            {"name": "多规格交付", "ratio": 0.0, "note": "9:16 主投 + 1:1 裁切", "due": "4/08"},
        ],
        "budget": {"total": 92000, "spent": 71500, "frozen": 8000},
        "links": {
            "meeting": "https://meeting.tencent.com/dm/demo-popcrew-brand",
            "group": "https://work.weixin.qq.com/?demo=popcrew-group-brand",
        },
        "timeline_ratios": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9, 0.62, 0.0],
        "ledger": [
            {"日期": "3/18", "类型": "音乐版权", "摘要": "贴片曲 15s 授权", "金额": "¥6,800", "状态": "已核准"},
            {"日期": "3/19", "类型": "包装", "摘要": "动态 LOGO 外包一版", "金额": "¥9,500", "状态": "已核准"},
            {"日期": "3/20", "类型": "调色", "摘要": "Look 预览工时包", "金额": "¥4,000", "状态": "已报销"},
            {"日期": "3/21", "类型": "修改", "摘要": "客户 v1 批注内免费轮次", "金额": "¥0", "状态": "已留痕"},
            {"日期": "3/22", "类型": "增项", "摘要": "多裁一版 1:1（待书面确认）", "金额": "¥2,200", "状态": "待确认"},
        ],
    },
    {
        "id": "demo_doc_series",
        "title": "门店纪实 · 短纪录片（3 集）",
        "phase": "策划复盘",
        "next_deadline": "4 月 10 日（第 2 集粗剪）",
        "health": "正常",
        "planning_summary": [
            "本周：补采店长访谈一条；第 1 集已定剪，进入简调色。",
            "风险：门店营业高峰时段收音杂讯，已加指向麦方案。",
        ],
        "members": [
            {
                "name": "陈牧野",
                "role": "导演",
                "progress": 0.5,
                "last_done": "昨日 16:00 与店长确认第 2 集故事线",
            },
            {
                "name": "赵砚舟",
                "role": "摄影",
                "progress": 0.58,
                "last_done": "今日 08:50 检查稳定器与备用电池",
            },
            {
                "name": "周予安",
                "role": "剪辑",
                "progress": 0.42,
                "last_done": "第 1 集 v0.9，待导演过片",
            },
        ],
        "milestones": [
            {"name": "选题与大纲", "ratio": 1.0, "note": "三集结构已批", "due": "3/01"},
            {"name": "第 1 集拍摄", "ratio": 1.0, "note": "素材入库", "due": "3/15"},
            {"name": "第 2 集拍摄", "ratio": 0.75, "note": "还差 1 次补采", "due": "3/30"},
            {"name": "粗剪 / 审片", "ratio": 0.35, "note": "第 1 集精剪前客户窗口", "due": "4/10"},
            {"name": "三集交付", "ratio": 0.0, "note": "统一片头片尾与字幕规范", "due": "4/28"},
        ],
        "budget": {"total": 68000, "spent": 31200, "frozen": 12000},
        "links": {
            "meeting": "https://meeting.tencent.com/dm/demo-popcrew-doc",
            "group": "https://work.weixin.qq.com/?demo=popcrew-group-doc",
        },
        "timeline_ratios": [1.0, 1.0, 0.95, 0.82, 0.72, 0.48, 0.35, 0.12, 0.0],
        "ledger": [
            {"日期": "3/17", "类型": "交通", "摘要": "补采日往返 + 器材车", "金额": "¥680", "状态": "已报销"},
            {"日期": "3/18", "类型": "场地", "摘要": "门店拍摄协调费", "金额": "¥2,000", "状态": "已核准"},
            {"日期": "3/19", "类型": "收音", "摘要": "指向麦租赁 2 天", "金额": "¥900", "状态": "已核准"},
            {"日期": "3/20", "类型": "餐饮", "摘要": "小团队工作餐", "金额": "¥420", "状态": "待复核"},
            {"日期": "3/21", "类型": "DIT", "摘要": "素材备份硬盘", "金额": "¥350", "状态": "已报销"},
        ],
    },
]


def _fmt_money_yuan(n: int) -> str:
    return f"{n:,} 元"


def render_budget_donut(bud: dict, spend_ratio: float) -> None:
    """环形图：预算总量在中心，已用 / 冻结 / 可用 三块扇区（演示）。"""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        st.caption("安装 **matplotlib** 后显示环形图：`pip install matplotlib`")
        st.progress(min(max(spend_ratio, 0.0), 1.0))
        return

    total = max(int(bud.get("total", 0)), 0)
    spent = max(min(int(bud.get("spent", 0)), total), 0) if total else 0
    frozen = max(min(int(bud.get("frozen", 0)), max(0, total - spent)), 0) if total else 0
    avail = max(0, total - spent - frozen)
    if total == 0:
        st.caption("（无预算总额，无法绘图）")
        return

    sizes = [spent, frozen, avail]
    labels = ["已用", "冻结", "可用"]
    colors = ["#e67e22", "#5dade2", "#2ecc71"]
    # 避免全 0 扇区导致绘图异常
    if sum(sizes) == 0:
        sizes = [1, 0, 0]
        colors = ["#bdc3c7", "#ecf0f1", "#ecf0f1"]

    fig, ax = plt.subplots(figsize=(2.65, 2.65), dpi=110)
    try:
        plt.rcParams["font.sans-serif"] = [
            "PingFang SC",
            "Heiti SC",
            "STHeiti",
            "SimHei",
            "Arial Unicode MS",
            "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception:
        pass

    ax.pie(
        sizes,
        labels=None,
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 1.2},
    )
    ax.text(
        0,
        0.06,
        "预算总额",
        ha="center",
        va="center",
        fontsize=9,
        color="#555",
    )
    ax.text(
        0,
        -0.1,
        f"{total:,}",
        ha="center",
        va="center",
        fontsize=13,
        fontweight="bold",
        color="#222",
    )
    ax.text(
        0,
        -0.26,
        "元（演示）",
        ha="center",
        va="center",
        fontsize=8,
        color="#777",
    )
    ax.set_aspect("equal")
    try:
        st.pyplot(fig, clear_figure=True, use_container_width=True)
    except TypeError:
        st.pyplot(fig, clear_figure=True)
    finally:
        plt.close(fig)

    leg = " · ".join(
        f"{lb} {_fmt_money_yuan(int(sz))}" for lb, sz in zip(labels, [spent, frozen, avail])
    )
    st.caption(leg)


# MV / 短片典型工序（左→右时间轴，与勘景踩点、筹备、拍摄、后期、交付对齐）
PIPELINE_PHASES: list[tuple[str, str, str]] = [
    ("kickoff", "立项", "立项策划：类型、周期、预算口径、交片窗口"),
    ("script", "脚本", "脚本与分镜：叙事结构、镜号表、节奏与转场"),
    ("scout", "勘景", "勘景踩点：场景锁定、光位与动线、备用景预案"),
    ("prep", "筹备", "筹备建组：Cast / 服化道 / 通告与 Call Sheet"),
    ("shoot", "拍摄", "拍摄执行：日戏·夜景、场记与素材回传节奏"),
    ("dit", "DIT", "DIT 与归档：双备份、哈希、场记对齐"),
    ("rough", "粗剪", "粗剪审片：v1 结构、客户批注与免费轮次边界"),
    ("online", "精剪", "精剪·调色·包装：字幕、多规格母版"),
    ("delivery", "交付", "成片交付：结项、源工程与素材移交清单"),
]


def _normalize_timeline_ratios(proj: dict) -> list[float]:
    raw = proj.get("timeline_ratios")
    if not raw:
        ms = proj.get("milestones") or []
        if not ms:
            return [0.0] * len(PIPELINE_PHASES)
        out = []
        n_ms = len(ms)
        for i in range(len(PIPELINE_PHASES)):
            if i < n_ms:
                out.append(min(max(float(ms[i].get("ratio", 0)), 0.0), 1.0))
            else:
                out.append(0.0)
        return out
    out = [min(max(float(x), 0.0), 1.0) for x in raw[: len(PIPELINE_PHASES)]]
    if len(out) < len(PIPELINE_PHASES):
        out.extend([0.0] * (len(PIPELINE_PHASES) - len(out)))
    return out


def _timeline_demo_excerpt(proj: dict, phase_idx: int, ratio: float) -> str:
    _pid, axis_lbl, blurb = PIPELINE_PHASES[phase_idx]
    title = (proj.get("title") or "本项目").strip()
    if ratio >= 0.999:
        status = "**本阶段：已完成**（演示）"
    elif ratio > 0:
        status = f"**本阶段：进行中** · 完成度约 {int(ratio * 100)}%（演示）"
    else:
        status = "**本阶段：未开始**（演示）"
    song = "《霓虹节拍》" if "MV" in title or "单曲" in title else "《未命名项目》"
    return (
        f"##### 企划书片段 · {axis_lbl} · {title}\n"
        f"{status}\n\n"
        f"- **工序说明**：{blurb}。\n"
        f"- **演示曲目/代号**：{song}（虚构，仅用于投资人看板）。\n"
        f"- **留痕**：群聊纪要、版本号与动账在 PopCrew 侧可勾稽；此处非正式合同或报价单。\n"
    )


def build_timeline_axis_html(ratios: list[float]) -> str:
    """横向数轴：左→右连线 + 节点颜色表示该阶段完成度。"""
    n = len(PIPELINE_PHASES)
    items = []
    for i in range(n):
        r = ratios[i] if i < len(ratios) else 0.0
        lbl = PIPELINE_PHASES[i][1]
        pct = int(round(r * 100))
        if r >= 0.999:
            cls = "tl-done"
        elif r > 0:
            cls = "tl-on"
        else:
            cls = "tl-todo"
        esc = html_module.escape(lbl)
        left_bar = "" if i == 0 else '<div class="tl-bar tl-bar-l"></div>'
        right_bar = "" if i == n - 1 else '<div class="tl-bar tl-bar-r"></div>'
        items.append(
            f'<div class="tl-item {cls}">'
            f'<div class="tl-row">{left_bar}<span class="tl-dot"></span>{right_bar}</div>'
            f'<div class="tl-lbl">{esc}</div>'
            f'<div class="tl-pct">{pct}%</div>'
            f"</div>"
        )
    inner = "".join(items)
    return f"""<div class="tl-axis-wrap"><div class="tl-axis">{inner}</div></div>
<style>
.tl-axis-wrap {{ width:100%; overflow-x:auto; padding:2px 0 6px; box-sizing:border-box; }}
.tl-axis {{ display:flex; align-items:flex-start; justify-content:space-between; min-width:min(100%, 640px); }}
.tl-item {{ flex:1; min-width:44px; text-align:center; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; font-size:10px; color:#31333F; }}
.tl-row {{ display:flex; align-items:center; height:14px; margin-bottom:2px; }}
.tl-bar {{ flex:1; height:2px; background:#dee2e6; min-width:2px; }}
.tl-dot {{ width:10px; height:10px; border-radius:50%; flex-shrink:0; border:2px solid #b8bcc4; background:#eceff3; }}
.tl-item.tl-done .tl-dot {{ background:#21a36f; border-color:#188a5e; }}
.tl-item.tl-on .tl-dot {{ background:#ffbd45; border-color:#d4941c; box-shadow:0 0 0 3px rgba(255,189,69,0.3); }}
.tl-item.tl-done .tl-bar {{ background:#a8e6cf; }}
.tl-item.tl-on .tl-bar-l {{ background:linear-gradient(90deg,#a8e6cf,#ffe08a); }}
.tl-item.tl-on .tl-bar-r {{ background:linear-gradient(90deg,#ffe08a,#dee2e6); }}
.tl-lbl {{ font-weight:600; line-height:1.2; margin-bottom:2px; }}
.tl-pct {{ opacity:0.78; font-size:9px; }}
</style>"""


def render_mv_timeline_panel(proj: dict) -> None:
    """全宽横向工序轴 + 点选阶段看企划书演示片段。"""
    ratios = _normalize_timeline_ratios(proj)
    st.caption(
        "**工序时间轴**（左→右：立项 → 勘景踩点 → 筹备 → 拍摄 → DIT → 粗剪 → 精剪 → **交付/结项**）"
    )
    st.markdown(build_timeline_axis_html(ratios), unsafe_allow_html=True)
    if ratios[-1] >= 0.999:
        st.success("**交付节点已完成** — 演示为结项状态（可对接成片签收与归档）。")
    opts = [f"{PIPELINE_PHASES[i][1]} · {PIPELINE_PHASES[i][2].split('：')[0]}" for i in range(len(PIPELINE_PHASES))]
    ix_list = list(range(len(PIPELINE_PHASES)))
    _tlk = f"pm_tl_{proj['id']}"
    try:
        pick = st.radio(
            "点选阶段 · 查看该段企划书摘要（演示）",
            ix_list,
            format_func=lambda i: opts[i],
            horizontal=True,
            key=_tlk,
        )
    except TypeError:
        pick = st.selectbox(
            "点选阶段 · 查看该段企划书摘要（演示）",
            ix_list,
            format_func=lambda i: opts[i],
            key=_tlk + "_sb",
        )
    with st.container(border=True):
        st.markdown(_timeline_demo_excerpt(proj, int(pick), ratios[int(pick)]))


# 「项目治理」标签页专用样式（挂 st.tabs key=workspace_tabs）
# 整体 zoom + 视口高度：面向 13" Mac 一屏可读（与「立项」中栏同为缩放思路）
PM_GOVERNANCE_TAB_CSS = """
<style>
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) {
    zoom: 0.74;
    max-height: calc(100vh - 4.25rem);
    overflow-y: auto;
    overflow-x: hidden;
    padding-right: 0.35rem;
    box-sizing: border-box;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2)::-webkit-scrollbar {
    width: 8px;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2)::-webkit-scrollbar-thumb {
    background: rgba(49, 51, 63, 0.28);
    border-radius: 4px;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) [data-testid="stVerticalBlock"] > [data-testid="element-container"] {
    margin-bottom: 0.12rem !important;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) h3,
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) h5 {
    font-size: 1rem !important;
    margin-top: 0.05rem !important;
    margin-bottom: 0.05rem !important;
    line-height: 1.25 !important;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) [data-testid="stCaption"] {
    font-size: 0.78rem !important;
    margin-top: 0 !important;
    margin-bottom: 0.1rem !important;
    line-height: 1.3 !important;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) [data-testid="stMetricValue"] {
    font-size: 1.05rem !important;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) [data-testid="stMetricLabel"] {
    font-size: 0.68rem !important;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) [data-testid="stProgress"] > div {
    height: 4px !important;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) [data-testid="stExpander"] details {
    padding-top: 0.18rem !important;
    padding-bottom: 0.18rem !important;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) [data-testid="stAlert"] {
    padding: 0.35rem 0.55rem !important;
    font-size: 0.82rem !important;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) [data-baseweb="radio"] label {
    font-size: 0.78rem !important;
}
div.st-key-workspace_tabs [role="tabpanel"]:nth-of-type(2) [data-testid="stMarkdownContainer"] p {
    margin-bottom: 0.35rem !important;
    font-size: 0.88rem !important;
    line-height: 1.35 !important;
}
</style>
"""


def render_project_governance_tab():
    """Tab「项目治理」：紧凑仪表盘 + 可展开账单（演示）。"""
    st.markdown(PM_GOVERNANCE_TAB_CSS, unsafe_allow_html=True)

    st.markdown("##### 项目治理仪表盘（演示）")
    st.caption("PopCrew · 13\" 级视口一屏总览（整页已缩放）；人 / 里程碑 / 资金 · 会议与群为占位链接")

    labels = [f"{p['title']} · {p['phase']}" for p in DEMO_ACTIVE_PROJECTS]
    ids = [p["id"] for p in DEMO_ACTIVE_PROJECTS]
    cur_id = st.session_state.pm_selected_project_id
    if cur_id not in ids:
        cur_id = ids[0]
        st.session_state.pm_selected_project_id = cur_id
    default_ix = ids.index(cur_id)

    sel_row_l, sel_row_r = st.columns([1.35, 2.65])
    with sel_row_l:
        choice_label = st.selectbox(
            "当前项目",
            labels,
            index=default_ix,
        )
    st.session_state.pm_selected_project_id = ids[labels.index(choice_label)]

    proj = next(p for p in DEMO_ACTIVE_PROJECTS if p["id"] == st.session_state.pm_selected_project_id)
    bud = proj["budget"]
    lk = proj.get("links") or {}
    spend_ratio = min(float(bud["spent"]) / float(bud["total"]), 1.0) if bud["total"] else 0.0
    nd = proj["next_deadline"] or ""
    nd_short = nd if len(nd) <= 12 else nd[:11] + "…"

    with sel_row_r:
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("阶段", proj["phase"])
        with m2:
            st.metric("下一节点", nd_short)
        with m3:
            st.metric("健康", proj["health"])
        with m4:
            st.metric("已用/预算", f"{int(spend_ratio * 100)}%")

    plan_one = " ｜ ".join(proj["planning_summary"])
    st.caption(f"**本周** {plan_one}")

    render_mv_timeline_panel(proj)

    main_l, main_r = st.columns([1.05, 1.0])
    with main_l:
        st.caption("**工序说明**")
        st.caption(
            "轴上从左到右即**一条片子的生命周期**：勘景踩点、筹备建组、拍摄、DIT、粗剪审片、精剪调色，直到**交付结项**。"
            " 绿点=该段已闭合，黄点=进行中，灰点=未开始。下方可选阶段查看「企划书」演示片段。"
        )

    with main_r:
        st.caption("**成员与动态**")
        mem_rows = [
            {
                "成员": m["name"],
                "岗位": m["role"],
                "%": int(float(m["progress"]) * 100),
                "最近": (m.get("last_done") or "")[:32] + ("…" if len(m.get("last_done") or "") > 32 else ""),
            }
            for m in proj["members"]
        ]
        st.dataframe(
            pd.DataFrame(mem_rows),
            hide_index=True,
            use_container_width=True,
            height=min(88 + len(mem_rows) * 28, 198),
        )

    st.caption("**项目资金池**")
    donut_col, metric_col = st.columns([1.15, 1.85])
    with donut_col:
        render_budget_donut(bud, spend_ratio)
    avail = int(bud["total"]) - int(bud["spent"]) - int(bud.get("frozen", 0))
    with metric_col:
        m1, m2 = st.columns(2)
        with m1:
            st.metric("预算总额", _fmt_money_yuan(int(bud["total"])))
            st.metric("已用", _fmt_money_yuan(int(bud["spent"])))
        with m2:
            st.metric("冻结 / 预留", _fmt_money_yuan(int(bud.get("frozen", 0))))
            st.metric("可用余额", _fmt_money_yuan(max(avail, 0)))
        st.caption(f"已用占预算：**{int(spend_ratio * 100)}%**（演示口径，与环形图一致）")

    act1, act2, act3 = st.columns([1, 1, 2])
    with act1:
        try:
            st.link_button(
                "发起会议",
                lk.get("meeting") or "https://meeting.tencent.com/",
                use_container_width=True,
                key=f"pm_lb_meet_{proj['id']}",
            )
        except TypeError:
            st.link_button(
                "发起会议",
                lk.get("meeting") or "https://meeting.tencent.com/",
                use_container_width=True,
            )
    with act2:
        try:
            st.link_button(
                "进项目群",
                lk.get("group") or "https://work.weixin.qq.com/",
                use_container_width=True,
                key=f"pm_lb_grp_{proj['id']}",
            )
        except TypeError:
            st.link_button(
                "进项目群",
                lk.get("group") or "https://work.weixin.qq.com/",
                use_container_width=True,
            )

    ledger = proj.get("ledger") or []
    with st.expander(f"账单 / 流水台账（演示）· 共 {len(ledger)} 条 — 点击展开", expanded=False):
        st.caption("与会议纪要、付款单勾稽为 PopCrew 资金白盒能力；下表为虚构演示。")
        if ledger:
            st.dataframe(
                pd.DataFrame(ledger),
                hide_index=True,
                use_container_width=True,
                height=min(112 + len(ledger) * 26, 252),
            )
        else:
            st.caption("（暂无演示行）")


def build_chat_iframe_html(messages: list) -> str:
    """左侧独立「对话框」：内部滚动，不把整页拉长。"""
    parts = []
    for m in messages:
        if m["role"] == "system":
            continue
        body = html_module.escape(m["content"]).replace("\n", "<br/>")
        if m["role"] == "user":
            parts.append(
                f'<div class="b user"><div class="who">Boss</div><div class="txt">{body}</div></div>'
            )
        else:
            parts.append(
                f'<div class="b asst"><div class="who">{html_module.escape(AI_FACE_NAME)}</div><div class="txt">{body}</div></div>'
            )
    inner = "".join(parts) or (
        '<div class="empty">还没有消息。可点下方<strong>快捷选项</strong>，或在输入框里描述你的项目。</div>'
    )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
html,body{{margin:0;padding:0;height:100%;background:transparent;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:14px;}}
.wrap{{height:100%;overflow-y:auto;overflow-x:hidden;padding:8px 6px 10px;box-sizing:border-box;}}
.b{{margin:0 0 8px;padding:8px 10px;border-radius:10px;line-height:1.45;font-size:14px;}}
.user{{background:rgba(255,75,75,0.09);margin-left:12%;border:1px solid rgba(255,75,75,0.14);}}
.asst{{background:rgba(255,200,80,0.2);margin-right:8%;border:1px solid rgba(200,150,40,0.18);}}
.who{{font-size:12px;font-weight:600;opacity:0.6;margin-bottom:5px;}}
.txt{{white-space:pre-wrap;word-break:break-word;}}
.empty{{opacity:0.5;font-size:14px;padding:20px 12px;text-align:center;line-height:1.5;}}
</style></head><body>
<div class="wrap" id="chatwrap">{inner}</div>
<script>
var w=document.getElementById("chatwrap");
if(w){{w.scrollTop=w.scrollHeight;}}
</script>
</body></html>"""


def finalize_cost_sheet_after_proposal(proposal_md: str) -> None:
    """企划书生成后：拉取 JSON 成本表，失败或残缺则用本地 fallback 补齐。"""
    raw = generate_cost_sheet_json(client, proposal_md)
    sheet = _normalize_cost_sheet(raw) if raw else {}
    fb = build_fallback_cost_sheet()
    if not sheet.get("people"):
        sheet = fb
    else:
        if len(sheet.get("other_costs") or []) < 5:
            sheet["other_costs"] = fb["other_costs"]
        if not sheet.get("total_range") or sheet.get("total_range") == "待核算":
            sheet["total_range"] = fb["total_range"]
        if not sheet.get("disclaimer"):
            sheet["disclaimer"] = fb["disclaimer"]
    st.session_state.cost_sheet = sheet
    st.session_state.cost_alt_idx = {}


# 4. 会话状态
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": build_system_content()}]
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": (
                "你好，我是**街灯 AI 制片助理**。我是你的 PopCrew 立项搭档：先聊聊**片型、场景、预算与风格**，"
                "信息够了我会帮你整理成**企划书 Markdown**；右侧还会粗估**班底与成本**（演示数据）。"
                "\n\n**这次想做什么类型的片子？**（可点下方快捷选项，或直接打字。）"
            ),
        }
    )

if "proposal" not in st.session_state:
    st.session_state.proposal = PROPOSAL_PLACEHOLDER_TEXT

if "intake_step" not in st.session_state:
    st.session_state.intake_step = 0

if "proposal_rev" not in st.session_state:
    st.session_state.proposal_rev = 0

if "crew_invited" not in st.session_state:
    st.session_state.crew_invited = set()

if "pm_selected_project_id" not in st.session_state:
    st.session_state.pm_selected_project_id = DEMO_ACTIVE_PROJECTS[0]["id"]

# 同步 system 段（AI 自主查漏，不随「第几步」改写）
def refresh_system_message():
    st.session_state.messages[0] = {"role": "system", "content": build_system_content()}


refresh_system_message()

st.caption("PopCrew 演示工作台 · 街灯 AI 制片")

tab_intake, tab_pm = st.tabs(["立项与企划", "项目治理"], key="workspace_tabs")

with tab_intake:
    # 仅作用于本 Tab 内三列工作台（避免「项目治理」里的 st.columns 吃到 zoom / 边框样式）
    st.markdown(
        """
        <style>
        section.main div.st-key-workspace_tabs [role="tabpanel"]:first-of-type [data-testid="stHorizontalBlock"] {
            align-items: flex-start !important;
        }
        section.main div.st-key-workspace_tabs [role="tabpanel"]:first-of-type [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(1) {
            padding-right: 0.75rem;
            border-right: 1px solid rgba(49, 51, 63, 0.12);
        }
        section.main div.st-key-workspace_tabs [role="tabpanel"]:first-of-type [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(1) h3 {
            margin-top: 0 !important;
            margin-bottom: 0.2rem !important;
        }
        section.main div.st-key-workspace_tabs [role="tabpanel"]:first-of-type [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(2) {
            max-height: min(920px, calc(100vh - 4rem)) !important;
            overflow-y: auto;
            overflow-x: hidden;
            padding-left: 0.5rem;
            padding-right: 0.6rem;
            border-right: 1px solid rgba(49, 51, 63, 0.1);
            zoom: 0.40;
        }
        section.main div.st-key-workspace_tabs [role="tabpanel"]:first-of-type [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(2)::-webkit-scrollbar {
            width: 8px;
        }
        section.main div.st-key-workspace_tabs [role="tabpanel"]:first-of-type [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(2)::-webkit-scrollbar-thumb {
            background: rgba(49, 51, 63, 0.28);
            border-radius: 4px;
        }
        section.main div.st-key-workspace_tabs [role="tabpanel"]:first-of-type [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3) {
            max-height: min(920px, calc(100vh - 4rem)) !important;
            overflow-y: auto;
            overflow-x: hidden;
            padding-left: 0.6rem;
        }
        section.main div.st-key-workspace_tabs [role="tabpanel"]:first-of-type [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3)::-webkit-scrollbar {
            width: 8px;
        }
        section.main div.st-key-workspace_tabs [role="tabpanel"]:first-of-type [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3)::-webkit-scrollbar-thumb {
            background: rgba(49, 51, 63, 0.28);
            border-radius: 4px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1.05, 1.0, 0.92])

    with col1:
        st.subheader("AI 制片沟通区")
        n_stages = len(INTAKE_STAGES)
        st.progress(min(st.session_state.intake_step / n_stages, 1.0))
        st.caption(
            f"**信息收集进度** {min(st.session_state.intake_step + 1, n_stages)} / {n_stages} 轮示意。"
            " 轮次走满后模型仍会追问细节；当你表达「可以生成企划」等意图时，会**自动**触发生成，也可随时点下方红钮**手动生成企划书**。"
        )

        components.html(
            build_chat_iframe_html(st.session_state.messages),
            height=CHAT_IFRAME_HEIGHT,
            scrolling=True,
        )
    
        activity_slot = st.empty()
    
        with st.expander("粘贴参考视频链接（可选）", expanded=False):
            st.caption(
                "支持 B 站等公开页链接；演示模式下会抽取**风格与节奏**要点写入对话，便于 AI 对齐预期。"
            )
            ref_url = st.text_input(
                "视频页链接",
                placeholder="https://www.bilibili.com/video/BV…",
                key="ref_url_input",
                label_visibility="collapsed",
            )
            if st.button("分析并写入对话", key="ref_url_go", use_container_width=True):
                u = (ref_url or "").strip()
                if not u:
                    st.warning("请先粘贴链接。")
                elif not (u.startswith("http://") or u.startswith("https://")):
                    st.warning("链接需以 http:// 或 https:// 开头。")
                else:
                    with st.spinner("处理链接中…"):
                        note = analyze_reference_url_style(client, u)
                    st.session_state.messages.append({"role": "user", "content": f"【参考链接】{u}"})
                    st.session_state.messages.append(
                        {"role": "assistant", "content": "【参考链接 · 演示】\n\n" + note}
                    )
                    refresh_system_message()
                    apply_quick_options_to_session(
                        st.session_state.intake_step, n_stages, st.session_state.intake_step >= n_stages
                    )
                    st.rerun()
    
        done_core = st.session_state.intake_step >= n_stages
    
        dq = st.session_state.get("dynamic_quick_options")
        dt = st.session_state.get("dynamic_topic_label", "")
        if isinstance(dq, list) and len(dq) >= 3:
            opts = dq
            stage_topic = dt or ("自由补充" if done_core else INTAKE_STAGES[min(st.session_state.intake_step, n_stages - 1)]["topic"])
        elif done_core:
            stage_topic = "自由补充"
            opts = [
                "还想补充：预算想再压一点",
                "拍摄想再加一个场景",
                "交片日期有变，再说一下",
                "没有别的了，出企划书吧",
            ]
        else:
            step = st.session_state.intake_step
            stage_topic = INTAKE_STAGES[step]["topic"]
            opts = INTAKE_STAGES[step]["options"]
    
        st.markdown(f"**快捷选项** · *{stage_topic}*")

        quick_reply = None
        key_ns = str(st.session_state.get("quick_options_rev", 0))
        for i, label in enumerate(opts):
            if st.button(label, key=f"quick_{key_ns}_{i}", use_container_width=True):
                quick_reply = label
    
        if st.button("可以了，生成企划书", type="primary", key="btn_proposal", use_container_width=True):
            quick_reply = "沟通得差不多了，请帮我总结并生成最终的项目企划书大纲。"
        if st.button("清空对话重来", key="btn_reset", use_container_width=True):
            st.session_state.messages = [{"role": "system", "content": build_system_content()}]
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": (
                        "你好，我是**街灯 AI 制片助理**。我们从头开始——**这次想做什么类型的片子？**"
                        "（可点快捷句或直接在下方输入。）"
                    ),
                }
            )
            st.session_state.proposal = PROPOSAL_PLACEHOLDER_TEXT
            st.session_state.intake_step = 0
            st.session_state.proposal_rev += 1
            st.session_state.crew_invited = set()
            st.session_state.pop("dynamic_topic_label", None)
            st.session_state.pop("dynamic_quick_options", None)
            st.session_state["quick_options_rev"] = 0
            st.session_state.pop("cost_sheet", None)
            st.session_state.pop("cost_alt_idx", None)
            st.rerun()
    
        user_input = st.chat_input("用一句话描述需求，或点上面的快捷选项…")
    
        if user_input or quick_reply:
            prompt = (user_input or quick_reply).strip()
            st.session_state.messages.append({"role": "user", "content": prompt})
    
            want_doc = is_proposal_request(prompt)
    
            full_response = ""
    
            if want_doc:
                run_proposal_generation(activity_slot, n_stages)
                st.rerun()
            else:
                # 每轮用户发言推进「轮次示意」条（上限 n_stages）；问什么由主对话模型自主决定
                if st.session_state.intake_step < n_stages:
                    st.session_state.intake_step = min(st.session_state.intake_step + 1, n_stages)
                refresh_system_message()

                try:
                    stream = client.chat.completions.create(
                        model="deepseek-chat",
                        messages=st.session_state.messages,
                        stream=True,
                    )
                    for chunk in stream:
                        if chunk.choices[0].delta.content:
                            full_response += chunk.choices[0].delta.content
                            vis = full_response.replace(AUTO_PROPOSAL_SIGNAL, "")
                            activity_slot.markdown(vis + "▌")
                except Exception as e:
                    activity_slot.error(f"对话请求失败：{e}")
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": f"（本次对话调用失败：{e}）请检查网络与 API Key 后重试。",
                        }
                    )
                    with st.spinner("正在刷新快捷选项…"):
                        apply_quick_options_to_session(
                            st.session_state.intake_step, n_stages, st.session_state.intake_step >= n_stages
                        )
                    st.rerun()

                full_clean, auto_proposal = split_auto_proposal_signal(full_response)
                if not (full_clean or "").strip():
                    full_clean = "（模型未返回有效内容，请重试或缩短问题。）"
                    auto_proposal = False
                activity_slot.markdown(full_clean)
                st.session_state.messages.append({"role": "assistant", "content": full_clean})
                if auto_proposal:
                    run_proposal_generation(activity_slot, n_stages)
                else:
                    with st.spinner("正在生成与当前追问对齐的快捷选项…"):
                        apply_quick_options_to_session(
                            st.session_state.intake_step, n_stages, st.session_state.intake_step >= n_stages
                        )
                st.rerun()

    with col2:
        st.subheader("项目企划书（实时预览）")
        st.caption("生成后为中栏 Markdown；人类 PM 可在下方源码区微调并保存。")
        prop = st.session_state.proposal or ""
        if is_proposal_placeholder(prop):
            st.info(prop)
        else:
            st.markdown(prop)
        with st.expander("编辑企划书源码（Markdown）", expanded=False):
            st.markdown("###### 文档预览 · 源码编辑")
            edited = st.text_area(
                "文档源码",
                value=st.session_state.proposal,
                height=360,
                key=f"proposal_ta_{st.session_state.proposal_rev}",
                label_visibility="collapsed",
            )
            if st.button("保存人类 PM 修改", key="btn_save_proposal"):
                st.session_state.proposal = edited
                st.session_state.proposal_rev += 1
                msg = "已保存。预览区与成本列将按最新企划刷新。"
                toast = getattr(st, "toast", None)
                if callable(toast):
                    toast(msg, icon="✅")
                else:
                    st.success(msg)
                st.rerun()

    with col3:
        st.subheader("成本估算 · 推荐班底")
        st.caption("企划书定稿后，本列拉取**分项成本 + 虚构班底卡片**（演示口径，非正式报价）。")
        prop3 = st.session_state.proposal or ""
        if is_proposal_placeholder(prop3):
            st.info(
                "企划书生成后，这里会展示**推荐班底**（可换一换 / 邀请占位）、**场地设备其它成本**表，以及**费用总预算区间**。"
            )
        else:
            if not st.session_state.get("cost_sheet"):
                finalize_cost_sheet_after_proposal(prop3)
            render_cost_sheet_column()

with tab_pm:
    render_project_governance_tab()
