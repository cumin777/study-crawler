"""引擎注册表：新增来源类型只需加一个模块并在这里注册。"""

from .base import RunSummary
from .forum import ForumEngine
from .gallery import GalleryEngine
from .linkhub import LinkHubEngine
from .video import VideoEngine

ENGINES = {
    "forum": ForumEngine,
    "linkhub": LinkHubEngine,
    "gallery": GalleryEngine,
    "video": VideoEngine,
}
