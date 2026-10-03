"""一个内容源把设备权限关在文档本身上。

站点主机和预览主机各自往响应头上写同一份答案：`_private` 是纯的，所以这里直接
问它，不用起服务、不用起库。

关在文档自己身上，而不是靠嵌入方 iframe 缺 `allow`：后者只在没人加 `allow`、
且一直跨源的时候成立；文档自己的策略只能往外减功能，所以页面无论怎么被打开都
一样。两处写的是同一个常量，谁单飞了这里就红。
"""

from starlette.responses import Response

from app.api.preview_host import _private as preview_response
from app.domain.site.hosting import _private as site_response

DEVICES = (
    "camera",
    "microphone",
    "geolocation",
    "display-capture",
    "accelerometer",
    "gyroscope",
    "magnetometer",
)


def test_both_content_origins_shut_every_device_on_the_document():
    site = site_response(Response()).headers["Permissions-Policy"]
    preview = preview_response(Response()).headers["Permissions-Policy"]
    assert site == preview, "两个内容主机不能各写一份"
    for device in DEVICES:
        assert f"{device}=()" in site, f"{device} 在内容源里还开着"
