"""Site-specific Windows media jobs are not distributed in this repository."""
class MediaSchedule:
    def __init__(self,store):self.store=store
    async def read(self):return None
    async def confirm(self,*args):raise ValueError('Site-specific integration is not installed')
    async def propose(self,*args):raise ValueError('Site-specific integration is not installed')
    async def recent(self):raise ValueError('Site-specific integration is not installed')
