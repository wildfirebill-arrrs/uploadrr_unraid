from .chevereto import CheveretoTemplate


class HAWKE(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "https://hawke.pics/api/1/upload", "hawke")
