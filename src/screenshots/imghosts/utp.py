from .chevereto import CheveretoTemplate


class UTP(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "https://utp.pm/api/1/upload", "utp")
