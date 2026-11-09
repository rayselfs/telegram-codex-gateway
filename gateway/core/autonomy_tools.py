"""Application-level owner-bound adapters; same-SID filesystem access can bypass them."""
import base64

SKILL_TOOL = {'type':'function','name':'gateway_shared_skill',
    'description':'List, publish, activate or withdraw your own shared skill; derive a new skill with source attribution. Publication only statically validates bytes and never runs scripts. Test in your workspace first.',
    'inputSchema':{'type':'object','properties':{
        'action':{'type':'string','enum':['list','publish','activate','withdraw']},
        'skill_id':{'type':'string'},'version':{'type':'string'},'archive':{'type':'string'},
        'name':{'type':'string'},'note':{'type':'string'},
        'source':{'type':'object','properties':{'skill_id':{'type':'string'},'version':{'type':'string'}},
                  'required':['skill_id','version'],'additionalProperties':False}},
        'required':['action'],'additionalProperties':False}}


class SkillTools:
    def __init__(self, store, verified_owner, *, enabled=False):
        self.store, self.owner, self.enabled = store, verified_owner, enabled

    async def apply(self, args):
        if not self.enabled:
            raise PermissionError('Shared publication is not enabled by maintenance policy')
        if not isinstance(args, dict): raise ValueError('Object required')
        action = args.get('action')
        allowed = {'list':set(), 'publish':{'archive','name','note','skill_id','source'},
                   'activate':{'skill_id','version'},'withdraw':{'skill_id'}}
        if action not in allowed or set(args)-{'action'}-allowed[action]:
            raise ValueError('Unsupported parameters; identity and paths cannot be supplied')
        if action == 'list': return self.store.listing()
        if action == 'publish':
            archive = args['archive']
            if not isinstance(archive,str) or len(archive)>12*1024*1024:
                raise ValueError('Archive exceeds transport limit')
            return self.store.publish(self.owner,base64.b64decode(archive,validate=True),
                **{k:v for k,v in args.items() if k not in ('action','archive')})
        if action == 'activate': self.store.activate(self.owner,args['skill_id'],args['version'])
        else: self.store.withdraw(self.owner,args['skill_id'])
        return {'state':'updated','skill_id':args['skill_id']}
