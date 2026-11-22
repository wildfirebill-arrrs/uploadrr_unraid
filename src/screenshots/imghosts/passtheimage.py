from .chevereto import CheveretoTemplate


class PASSTHEIMAGE(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "https://passtheima.ge/api/1/upload", "passtheimage")
