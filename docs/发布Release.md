# 发布与贡献

## 开发验证

```bash
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
(cd server && ../.venv/bin/python -m unittest discover -s tests)
.venv/bin/python -m unittest discover -s deploy/tests
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm test && pnpm build)
```

CI 还会构建真正的 Docker 镜像，启动全新容器，检查健康、鉴权及匿名曲库 401。源码和安装包不包含作者曲库、Cookie、服务器 IP、`.env` 或旧仓库 Git 历史。现有行为和迁移用例保留在 `server/tests/` 与 `web/tests/`。

## 发布新版本

1. 修改 `VERSION`，例如 `1.0.1`，同步 API 的版本信息；新增 `docs/releases/v1.0.1.md` 描述变化、验证和升级注意事项。
2. 提交并推送到 `main`，确认 Checks 通过。
3. 打版本标签并推送：

```bash
git tag v1.0.1
git push origin v1.0.1
```

`Release` 工作流先跑完整 CI，再用 `scripts/package_release.py` 生成并上传：

- `bilimusicplayer.tar.gz`：完整可构建源码与 Docker 编排、教程。
- `install.sh`：Linux 一键安装 / 更新入口，支持自动安装 Docker。
- `SHA256SUMS`：发布文件的 SHA-256。

安装器的默认地址始终指向最新正式版，也可以 `--version v1.0.1` 固定版本。发布需要仓库启用 Actions；工作流自行申请 `contents: write` 权限，无需额外个人令牌。版本标签与 `VERSION` 不同、检查失败时不发布。

手动检查打包内容：

```bash
python3 scripts/package_release.py
(cd release-dist && sha256sum -c SHA256SUMS)
```

打包器只打包 Git 跟踪文件，并拒绝跟踪的运行数据和符号链接；新文件应先提交再打包。不要上传工作目录的整体压缩文件。

## 许可证和依赖

项目采用 [MIT](../LICENSE)：允许使用、修改、部署和分发，须保留原版权与许可证声明。MIT 适用于本仓库原创代码；React、FastAPI、yt-dlp、Navidrome、Caddy、FFmpeg 等依赖分别遵循其自身许可证，见 [第三方组件](../THIRD_PARTY.md)。发布本项目不包含或授权传播任何下载的音乐内容。
