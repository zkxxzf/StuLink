# StuLink v1.9.2 2026-09-16
# AI 服务商注册表：统一维护 base_url / 可选模型 / 默认模型 / 鉴权方式
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""支持的 AI 服务商（全部为 OpenAI 兼容的 /chat/completions 协议）

新增服务商只要在这里加一条即可，前端下拉、默认模型、接口地址自动生效。
"""

# base_url 不含 /chat/completions，调用时自动拼接
PROVIDERS = {
    'deepseek': {
        'name': 'DeepSeek',
        'base_url': 'https://api.deepseek.com',
        'default_model': 'deepseek-chat',
        'models': ['deepseek-chat', 'deepseek-reasoner'],
        'key_hint': 'sk-xxxxxxxx',
        'docs': 'https://platform.deepseek.com/api_keys',
    },
    'qwen': {
        'name': '通义千问（阿里云百炼）',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'default_model': 'qwen-plus',
        'models': ['qwen-plus', 'qwen-turbo', 'qwen-max', 'qwen-long', 'qwen2.5-72b-instruct'],
        'key_hint': 'sk-xxxxxxxx',
        'docs': 'https://bailian.console.aliyun.com/?tab=model#/api-key',
    },
    'zhipu': {
        'name': '智谱 GLM',
        'base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'default_model': 'glm-4-flash',
        'models': ['glm-4-flash', 'glm-4-flashx', 'glm-4-air', 'glm-4-plus', 'glm-4-long'],
        'key_hint': 'xxxxxxxx.xxxxxxxx',
        'docs': 'https://open.bigmodel.cn/usercenter/apikeys',
    },
    'moonshot': {
        'name': 'Kimi（月之暗面）',
        'base_url': 'https://api.moonshot.cn/v1',
        'default_model': 'moonshot-v1-8k',
        'models': ['moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k'],
        'key_hint': 'sk-xxxxxxxx',
        'docs': 'https://platform.moonshot.cn/console/api-keys',
    },
    'doubao': {
        'name': '豆包（火山方舟）',
        'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
        'default_model': 'doubao-1-5-pro-32k-250115',
        'models': ['doubao-1-5-pro-32k-250115', 'doubao-1-5-lite-32k-250115',
                   'doubao-1-5-pro-256k-250115', 'doubao-pro-32k-241215'],
        'key_hint': 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
        'docs': 'https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey',
        'note': '也可直接填方舟的接入点 ID（ep-xxxx）',
    },
    'hunyuan': {
        'name': '腾讯混元',
        'base_url': 'https://api.hunyuan.cloud.tencent.com/v1',
        'default_model': 'hunyuan-turbo',
        'models': ['hunyuan-turbo', 'hunyuan-pro', 'hunyuan-standard', 'hunyuan-lite'],
        'key_hint': 'sk-xxxxxxxx',
        'docs': 'https://cloud.tencent.com/product/hunyuan',
    },
    'qianfan': {
        'name': '文心一言（百度千帆）',
        'base_url': 'https://qianfan.baidubce.com/v2',
        'default_model': 'ernie-4.0-8k',
        'models': ['ernie-4.0-8k', 'ernie-4.0-turbo-8k', 'ernie-3.5-8k', 'ernie-speed-8k'],
        'key_hint': 'bce-v3/ALTAK-xxxxxxxx',
        'docs': 'https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application',
    },
    'custom': {
        'name': '自定义（OpenAI 兼容）',
        'base_url': '',
        'default_model': '',
        'models': [],
        'key_hint': '由服务商提供',
        'docs': '',
        'note': '任意 OpenAI 兼容接口（含本地/中转站），需自填接口地址与模型名',
    },
}

DEFAULT_PROVIDER = 'deepseek'
CUSTOM_PROVIDER = 'custom'


def get_provider(provider):
    """取服务商配置；未知值按 custom 处理（返回可写副本）"""
    key = (provider or DEFAULT_PROVIDER).strip().lower()
    if key in PROVIDERS:
        return dict(PROVIDERS[key])
    return dict(PROVIDERS[CUSTOM_PROVIDER])


def provider_exists(provider):
    return (provider or '').strip().lower() in PROVIDERS


def default_model_of(provider):
    return get_provider(provider)['default_model']


def resolve_base_url(provider, base_url=''):
    """最终接口地址：用户填写优先，否则用服务商默认"""
    base_url = (base_url or '').strip().rstrip('/')
    if base_url:
        return base_url
    return get_provider(provider)['base_url']


def resolve_model(provider, model=''):
    """最终模型名：用户填写优先，否则用服务商默认，再兜底 deepseek-chat"""
    model = (model or '').strip()
    if model:
        return model
    return default_model_of(provider) or PROVIDERS[DEFAULT_PROVIDER]['default_model']


def list_providers():
    """供前端下拉使用"""
    return [{
        'key': k,
        'name': v['name'],
        'base_url': v['base_url'],
        'default_model': v['default_model'],
        'models': v['models'],
        'key_hint': v.get('key_hint', ''),
        'docs': v.get('docs', ''),
        'note': v.get('note', ''),
    } for k, v in PROVIDERS.items()]


def validate_api_key(api_key):
    """Key 基础格式校验，返回 (ok, 错误信息)

    只做本地可判定的检查（长度/字符集），不联网；
    真正的有效性由「测试连接」调用厂商接口确认。
    """
    api_key = (api_key or '').strip()
    if not api_key:
        return False, '请输入 API Key'
    if len(api_key) < 8:
        return False, 'API Key 太短（至少 8 位），请确认复制完整'
    if len(api_key) > 300:
        return False, 'API Key 超长（>300 字符），请检查是否复制了多余内容'
    if any(ch.isspace() for ch in api_key):
        return False, 'API Key 中不能包含空格或换行，请检查复制内容'
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in api_key):
        return False, 'API Key 含不可见字符，请重新复制'
    return True, ''
