# 第三方组件

本仓库原创代码使用 MIT。依赖组件遵循各自许可证，MIT 不替代这些许可证。安装时由包管理器 / Docker 获取第三方组件；发行包不含音乐、Cookie 或用户数据。

| 组件 | 作用 | 许可证 / 官方来源 |
| --- | --- | --- |
| React / Vite / Tailwind CSS / Lucide | 网页界面 | 各项目 MIT，参见其源码及包内 LICENSE |
| FastAPI / Pydantic / HTTPX / Mutagen / Pillow / qrcode | 后端、标签、图片及验证器二维码 | 以安装版本随包 LICENSE 为准（Mutagen 为 GPL，Pillow 为 HPND） |
| yt-dlp | 音频下载回退 | [官方许可证说明](https://github.com/yt-dlp/yt-dlp#license)，源码 Unlicense，部分发行附件另有条款 |
| Navidrome | Subsonic 曲库服务器，独立容器 | [GPL-3.0](https://github.com/navidrome/navidrome/blob/master/LICENSE) |
| Caddy | HTTPS 反向代理，独立容器 | [Apache-2.0](https://github.com/caddyserver/caddy/blob/master/LICENSE) |
| FFmpeg | 音轨无损封装、验证 | [官方许可说明](https://ffmpeg.org/legal.html)，取决于发行版编译选项 |

再发布自定义镜像或第三方二进制时，应一并满足对应依赖许可证及源码提供要求。
