# 🎵 BiliMusicPlayer — B站收藏夹歌曲库

粘贴一个 Bilibili（B站）收藏夹链接，自动完成：

1. **解析收藏夹** — 拉取收藏夹内全部视频（公开列表免登录，私密列表粘贴 Cookie）
2. **提取元数据** — 从标题/标签智能解析歌曲名与歌手（虚拟主播翻唱优化），可预览修改
3. **分P支持** — 多P视频内联展开每一P（标题+时长）供你选择，下载指定分P
4. **可控歌词** — LRCLIB → 网易云 → QQ 音乐三级降级；导入时可关闭，支持上传/替换 `.lrc`/`.txt`，播放器可隐藏
5. **受控下载** — 全局共享下载队列（默认 3、硬上限 5），大收藏夹也会依次消化，避免多任务叠加打爆 B 站 API
6. **无损保存源音轨** — 保留 B 站原始 AAC/M4A，不二次编码；封面统一压到 320px JPEG，兼顾 iPhone/网页兼容性与磁盘占用
7. **在线播放** — 网页播放器（歌词滚动高亮、编辑、删除）+ iOS 免费 App「Amperfy」
8. **去重与备份** — 记录每首歌的 B 站源链接，重复导入自动跳过；一键导出 CSV

## 架构

```
┌─────────────┐   ┌──────────────────────────────────┐   ┌──────────────┐
│  网页播放器   │──▶│  你的服务器 (Docker)                │──▶│  iOS App      │
│  React 前端  │   │  ├─ api: FastAPI(解析/下载/歌词/网页) │   │  Amperfy 等   │
└─────────────┘   │  ├─ navidrome: 曲库服务(Subsonic)   │   │  (免费/国区)   │
                  │  └─ SQLite 曲库 + 音频 + .lrc 歌词  │   └──────────────┘
                  └──────────────────────────────────┘
```

- **web/** — React + Vite + Tailwind v4 前端（播放器、歌词、导入界面、编辑/删除）
- **server/** — FastAPI 后端（B站解析、元数据提取、DASH 下载、歌词匹配、曲库 API）
- **docker-compose.yml** — 服务器部署编排（api + navidrome + caddy 自动 HTTPS）
- **deploy/** — 部署脚本与配置模板

## 文档

| 文档 | 内容 |
|---|---|
| [docs/使用指南.md](docs/使用指南.md) | 从导入到播放的完整操作指南（含私密收藏夹 Cookie、分P选择、iOS） |
| [docs/部署运维.md](docs/部署运维.md) | 服务器部署、更新、域名/HTTPS、备份与恢复、常见运维 |
| [docs/账户安全.md](docs/账户安全.md) | 管理员用户名/密码设置、可选 2FA、丢失验证器后的恢复 |
| [docs/发布Release.md](docs/发布Release.md) | 构建验证、版本发布、Release 安装包 |
| [docs/API.md](docs/API.md) | 后端 API 参考（认证、端点、数据结构） |
| [bilibili-research-report.md](bilibili-research-report.md) | B 站 API 实测调研报告（收藏夹接口、412 风控、歌词源） |

## 快速开始（本地开发）

```bash
# 后端（Python 3.11+，需 ffmpeg）
cd server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8080

# 前端（自动代理 /api → localhost:8080）
cd web
pnpm install && pnpm dev
```

## 服务器部署

先将自己的两个子域名（例如 `music.example.com` 和 `navi.example.com`）解析到 Linux 服务器，放行 80 / 443，然后在服务器执行。**没有 Docker 也可以：脚本会先安装 Docker，再下载最新 Release 并部署。**

```bash
curl -fsSL https://github.com/Sakuranda/BiliMusicPlayer/releases/latest/download/install.sh | sudo bash -s -- --domain music.example.com --navidrome-domain navi.example.com --install-docker
```

把两个域名换成自己的；root 用户可去掉 `sudo`。首次构建需要几分钟，HTTPS 证书自动申请。默认安装到 `/opt/bilimusicplayer`，初始管理员用户名 `admin`，随机密码在服务器 `deploy/.env` 中。登录后在「设置」修改用户名/密码，按需开启 **2FA（默认关闭）**。

| 用途 | 自己的地址 |
|---|---|
| 网页播放器（HTTPS） | `https://music.example.com` |
| iOS 曲库服务 (Subsonic) | `https://navi.example.com` |

详细安装、已有反向代理、指定版本、升级和备份见 [部署教程](docs/部署运维.md)。安装包见 [Releases](https://github.com/Sakuranda/BiliMusicPlayer/releases)。

升级一条命令（保留歌曲、配置和账户）：

```bash
curl -fsSL https://github.com/Sakuranda/BiliMusicPlayer/releases/latest/download/install.sh | sudo bash -s -- --dir /opt/bilimusicplayer
```

## iOS 播放（Amperfy）

1. App Store（国区）下载免费的 [Amperfy](https://apps.apple.com/cn/app/amperfy-music/id1530145038)
2. 添加服务器：协议 **Subsonic**，地址 `https://navi.example.com`
3. 填 Navidrome 账号密码，支持在线播放、歌词（.lrc）、离线缓存、CarPlay

## 支持的收藏夹链接格式

- `https://space.bilibili.com/{mid}/favlist?fid={fid}`（电脑端地址栏）
- `https://www.bilibili.com/medialist/detail/ml{id}`（新版页面复制链接）
- `https://b23.tv/xxx`（手机分享短链，自动展开）

私密收藏夹需在导入时粘贴浏览器 Cookie（`SESSDATA=...`），见使用指南。

## 常见问题速查

- **私密收藏夹**：导入页展开「粘贴 B 站 Cookie」
- **下载失败 412 / bad parameter**：B 站风控限流，系统已内置全局限速、最多 5 路并发、多级等待与自动重试，一般无需干预
- **歌词没有**：翻唱曲目在歌词库常查不到，属正常现象；可在歌曲编辑弹窗上传 `.lrc` / `.txt`，不需要歌词也可在导入时关闭自动匹配
- **磁盘**：音频保留 B 站原始 AAC/M4A（不二次转码），封面只存 320px 低质量 JPEG；重下会清理被替换的旧文件

## 许可证

[MIT](LICENSE)：允许使用、修改、部署和分发，须保留版权与许可证声明。第三方组件遵循[各自许可证](THIRD_PARTY.md)。
