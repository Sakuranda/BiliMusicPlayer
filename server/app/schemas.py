"""Pydantic 请求/响应模型。"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ImportRequest(BaseModel):
    url: str
    cookie: Optional[str] = None       # 收藏夹为私密时需提供 B 站 Cookie
    album: Optional[str] = None        # 专辑名，默认取收藏夹标题
    save_collection: bool = False      # 保存链接，供手动/自动增量更新
    multipart_policy: Literal["ask", "first", "all"] = "ask"
    auto_update: bool = False          # 每 24 小时自动同步并下载新增歌曲


class SongUpdate(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    cid: Optional[int] = Field(default=None, gt=0)          # 选中的分P
    part_index: Optional[int] = Field(default=None, gt=0)
    part_title: Optional[str] = None
    duration: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    lyrics_enabled: Optional[bool] = None

    @field_validator("title", "artist", "album")
    @classmethod
    def clean_metadata(cls, value, info):
        if value is None:
            return value
        value = value.strip()
        if (info.field_name != "album" and not value) or len(value) > 300:
            raise ValueError("歌名和歌手不能为空，歌曲信息最长 300 字")
        return value


class StartRequest(BaseModel):
    # 可选：只下载部分 bvid（用户勾选）
    bvids: Optional[list[str]] = None
    song_ids: Optional[list[int]] = None
    fetch_lyrics: bool = True


class PlaylistWrite(BaseModel):
    name: str


class PartSelection(BaseModel):
    song_ids: list[int]
    bvids: list[str]  # Explicitly reviewed videos, including those skipped entirely


class CollectionUpdate(BaseModel):
    multipart_policy: Optional[Literal["ask", "first", "all"]] = None
    auto_update: Optional[bool] = None
    fetch_lyrics: Optional[bool] = None


class LoginRequest(BaseModel):
    username: str = Field(max_length=80)
    password: str = Field(max_length=256)
    captcha_id: str
    captcha: str
    totp_code: str = Field(default='', max_length=6)


class SecurityUpdate(BaseModel):
    current_password: str = Field(max_length=256)
    code: str = Field(default='', max_length=6)
    username: Optional[str] = Field(default=None, max_length=80)
    new_password: Optional[str] = Field(default=None, max_length=256)
