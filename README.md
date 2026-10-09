# 買物（kaimono）

一个只为自己利益服务的「买东西」App：记订单、按送达时间提醒在家收货、汇总自己写的盯货盯价脚本的状态，并让 AI 按自己的偏好和消费记录推荐商品与服务——不接返利，不接广告。

macOS 上是一个 WKWebView 外壳（`買物.app`），背后是一个只监听 `127.0.0.1` 的 Python 标准库服务；手机或平板可以经自己的内网（如 Tailscale）用浏览器访问同一个页面。

## 为什么做这个

这是「个人操作系统」系列中的一个 App。这个系列只有一个目标：把我和外界之间每一次信息往来的动作和历史都留在自己手里——看了什么、在哪里停留了多久、点了什么、买了什么、卖了什么、看完之后做了什么。

背后的想法是：个人对外部世界的理解好比一滴水去理解大海，几乎不可能做到。但一滴水向内看，看清自己的每个分子怎么动，是做得到的——什么温度下我会怎么动，遇到什么潮汐、什么洋流又会怎么动。把这些搞清楚，就能为自己争取更好的生存条件。这比理解整个大海现实得多。

所以这个系列的共同约定是：所有行为记录写进本机数据库，不上传任何第三方；AI 分析在本机或用户自己选择的服务上运行。

## 功能

- **订单与收货提醒**：手记订单（商家、物品、送达日期、时间窗、快递单号）。到货当天按时间窗提前 2 小时、20 分钟各提醒一次（系统通知 + `say` 朗读 + 可选邮件）；只写了日期的当天 09:00 提醒。
- **盯货清单**：只读展示你自己写的哨兵脚本的状态（launchd 是否在跑、state 文件、最后一行日志），清单写在 `watchers.json`。
- **荐物**：调用本机 `claude` CLI 联网出 6–8 条推荐，每条必须给出处 URL，服务端核验能打开；随后逐条核价（现价、运费、到手总价），推荐理由必须点名依据（偏好档案、消费记录、过往反馈）。
- **事件表**：App 内每个动作写进 `events` 表，方便以后分析自己。

## 运行

依赖：macOS、Python 3.11+（只用标准库）、荐物功能需要已登录的 [Claude Code](https://claude.com/claude-code) CLI。

```sh
python3 server/app.py              # 服务：http://127.0.0.1:8470/
python3 server/remind.py           # 收货提醒（建议每 5 分钟由 launchd 跑一次）
python3 server/recommend.py generate --if-stale   # 出一批荐物
zsh macapp/build.sh                # 编译 build/買物.app
python3 -m pytest -q               # 测试
```

`launchd/com.example.shopping.plist` 是常驻服务的示例。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `SHOPPING_DATA` | `~/Library/Application Support/shopping` | 数据目录（SQLite、偏好档案、watchers.json） |
| `SHOPPING_PORT` | `8470` | 服务端口 |
| `SHOPPING_LEDGER` | `<数据目录>/ledger.csv` | 可选的家庭消费账本 CSV，荐物读它的摘要 |
| `SHOPPING_CLAUDE` | `PATH` 里的 `claude` | Claude Code CLI 路径 |
| `SHOPPING_CITY` | `东京` | 季节与运费按哪个城市算 |
| `SHOPPING_SAY_VOICE` | `Tingting` | 朗读提醒用的语音 |

邮件提醒是可选的：在 `PYTHONPATH` 里放一个提供 `send_email(subject, body)` 的 `notify.py` 即可，没有就跳过。

## 隐私说明

订单、反馈、事件全部存在本机 SQLite。荐物时会把偏好档案、反馈摘要、消费记录摘要和在途订单发给你配置的 Claude；不想发就不要运行荐物。

## 局限

这是作者在自己机器上日用的工具，按自己的习惯写的（中文界面、日本的购物与物流）。欢迎拿去改。

## 许可证

GPL-3.0
