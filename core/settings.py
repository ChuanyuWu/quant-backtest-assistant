"""用户设置管理：数据源Token、AI服务商配置等，存于 data/config.json"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.app_config import AppConfig
from core.data_io import load_json, save_json

# AI 服务商配置（全部为 OpenAI 兼容接口）
AI_PROVIDERS = {
    "siliconflow": {
        "name": "硅基流动",
        "url": "https://api.siliconflow.cn/v1/chat/completions",
        "default_model": "deepseek-ai/DeepSeek-V3",
        "site": "siliconflow.cn",
        "note": "注册送额度，小模型免费",
    },
    "deepseek": {
        "name": "DeepSeek官方",
        "url": "https://api.deepseek.com/chat/completions",
        "default_model": "deepseek-chat",
        "site": "platform.deepseek.com",
        "note": "价格便宜，充值即用",
    },
    "zhipu": {
        "name": "智谱GLM",
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "default_model": "glm-4.7-flash",
        "site": "bigmodel.cn",
        "note": "glm-4.7-flash 免费（旗舰普惠版）",
    },
    "dashscope": {
        "name": "阿里百炼",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "default_model": "qwen-plus",
        "site": "bailian.console.aliyun.com",
        "note": "新用户送百万token",
    },
    "moonshot": {
        "name": "月之暗面Kimi",
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "default_model": "moonshot-v1-8k",
        "site": "platform.moonshot.cn",
        "note": "长文本能力强",
    },
}

DEFAULT_SETTINGS = {
    "tushare_token": "",
    "sf_api_key": "",          # AI 服务的 API Key（字段名保持兼容）
    "ai_provider": "siliconflow",
    "ai_model": "",            # 留空则用服务商默认模型
    "initial_cash": 100000,
    "data_source": "akshare",
    "auto_update": False,      # 每日收盘后自动增量更新本地股票数据
}


# 密钥类配置允许用环境变量提供，避免明文写在 config.json 里随项目外发
_ENV_FALLBACK = {
    "tushare_token": "TUSHARE_TOKEN",
    "sf_api_key": "SF_API_KEY",
}


def get_settings() -> dict:
    cfg = load_json(AppConfig.CONFIG_FILE, {})
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in cfg.items() if v is not None})
    # 文件里没有的密钥，从环境变量兜底
    for key, env in _ENV_FALLBACK.items():
        if not merged.get(key):
            merged[key] = os.environ.get(env, "")
    return merged


def update_settings(updates: dict) -> dict:
    # 注意：从原始文件读，不要把环境变量兜底来的密钥写回文件
    raw = load_json(AppConfig.CONFIG_FILE, {})
    cfg = dict(DEFAULT_SETTINGS)
    cfg.update({k: v for k, v in raw.items() if v is not None})
    for k in DEFAULT_SETTINGS:
        if k in updates and updates[k] is not None:
            cfg[k] = updates[k]
    save_json(AppConfig.CONFIG_FILE, cfg)
    return cfg


def get_ai_config() -> dict:
    """返回当前生效的AI配置 {url, model, api_key, provider_name}"""
    s = get_settings()
    provider = AI_PROVIDERS.get(s.get("ai_provider"), AI_PROVIDERS["siliconflow"])
    return {
        "url": provider["url"],
        "model": s.get("ai_model") or provider["default_model"],
        "api_key": s.get("sf_api_key", "").strip() or os.environ.get("SF_API_KEY", ""),
        "provider_name": provider["name"],
        "site": provider["site"],
    }
