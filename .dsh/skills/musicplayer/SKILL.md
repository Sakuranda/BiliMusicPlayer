---
name: musicplayer
description: BiliMusicPlayer 的开发、测试和公开发行工作流。
---

# BiliMusicPlayer 项目技能

先读根目录 `AGENTS.md`。代码架构与部署说明见 README / docs。

## 工作流

实现 → 有意义的后端 / 前端 / 安装回归 → Docker 构建与鉴权验证 → 更新文档和项目记忆 → 提交。发布先确认 VERSION 与标签相同，再推标签，由 Actions 创建 Release。公网安装与更新使用 `deploy/install.sh`，本地源码用 `deploy/deploy.sh`。不依赖任何作者私有服务器。

## 历史踩坑

- SQLite 独立线程连接 + WAL，迁移保留旧歌曲 ID 与关系；现有音频文件不能因脏状态重复下载。
- 多 P 的下载单位是歌曲 ID，旧记录补齐后重新选择；未核对的 P 不自动下载。
- 用共享下载队列和歌曲占用，文件 / 标签 / 歌词在同目录原子替换；音轨无损 remux 为 faststart，核对所选 CID 时长。
- Media Session 稳定 handler + 同步 ref，切歌不撤销上一曲元数据；解析器不盲猜切片 UP 为歌手。
- 2026-10-11 公开迁移只复制当前源码，不复制旧 Git 历史、数据、Cookie、env 或私人验收记录；Release 只打包跟踪文件并拒绝敏感路径。
- 2026-10-11 安装自动生成随机密码，Compose 显式 --env-file，不 source env；升级保留数据且构建后再查下载，数据库备份先于重启。
- 2026-10-11 管理员更新存密码摘要、轮换会话；TOTP 绑定先验证，登录验证 / 防重放同事务，服务器本地恢复保留曲库。
- 2026-10-11 相同参数重试不得重置配置，空数组需兼容 Bash nounset；CI 实跑全新 Compose 安装、升级保留、数据库备份与 Caddy 校验。
