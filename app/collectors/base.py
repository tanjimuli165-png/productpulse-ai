from abc import ABC, abstractmethod
from typing import List
from app.database.models import Evidence


class BaseCollector(ABC):
    name = "base"

    @abstractmethod
    def collect(self, topic: str, limit: int = 25) -> List[Evidence]:
        """Collect normalized evidence for a topic."""
        raise NotImplementedError
