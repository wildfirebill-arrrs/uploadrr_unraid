from .chevereto import CheveretoTemplate


class HAMSTER(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "https://hamsterimg.net/api/1/upload", "hamster")
        self.adult_only = True
