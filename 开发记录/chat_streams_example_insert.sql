-- chat_streams 示例数据插入（与 target_selector / heartbeat_v2 等使用的表一致）
-- 表结构见 src/common/database/database_model.py ChatStreams
-- 空列以 NULL 写入；若数据库将空字符串与 NULL 区分，可自行将 NULL 改为 ''

INSERT INTO chat_streams (
    stream_id,
    create_time,
    group_platform,
    group_id,
    group_name,
    last_active_time,
    platform,
    user_platform,
    user_id,
    user_nickname,
    user_cardname
) VALUES
(
    '9a20631ce1207c1a101d148c37f4a268',
    1769851296.0012112,
    NULL,
    NULL,
    NULL,
    1770282932.3649108,
    'qq',
    'qq',
    '2848675459',
    '小zz',
    '欢欢'
),
(
    '743929f8b1c629a3a5ebc49447273881',
    1771987217.3188417,
    'webui',
    'webui_local_chat',
    'WebUI本地聊天室',
    1772091124.4414785,
    'webui',
    'webui',
    'webui_user_webui_8cafpfgxy_mklzxtbc',
    '空梦',
    '空梦'
),
(
    'e85055007c3125f2ec48b6f411ed6c6e',
    1768046189.962568,
    'qq',
    '971608345',
    'PaiMengUNIVERSITY',
    1772941514.4188247,
    'qq',
    'qq',
    '939064316',
    '溟e',
    '无能的群友'
),
(
    'aebc11c675c1b7ce09e00298dd897292',
    1768672234.8571787,
    NULL,
    NULL,
    NULL,
    1773382635.771811,
    'qq',
    'qq',
    '1962560763',
    '空梦',
    '主人, 空梦大大, 彳亍'
),
(
    '8e506d5cf35fc1428dc51700505cc489',
    1768041702.3179367,
    'qq',
    '877283684',
    'Penacony Paperfold University College',
    1773382683.1472824,
    'qq',
    'qq',
    '1962560763',
    '空梦',
    '尔多龙喵？（泰姆菲尔德家の爱犬）'
);
