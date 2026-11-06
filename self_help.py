"""Fixed current-worker operations, with no arbitrary broker command runner."""
TOOL={'type':'function','name':'gateway_self_help',
      'description':'Inspect own worker health, reload skills, reconnect own configured tools, or request own worker restart after the current turn ends. Repair scripts/dependencies directly in your personal workspace/venv, never in the shared runtime.',
      'inputSchema':{'type':'object','properties':{'action':{'type':'string','enum':[
          'health','reload_skills','reconnect_tools','request_restart']}},
          'required':['action'],'additionalProperties':False}}


class SelfHelp:
    def __init__(self,controller,supervisor):
        self.c,self.supervisor=controller,supervisor

    async def apply(self,args):
        actions={'health','reload_skills','reconnect_tools','request_restart'}
        if not isinstance(args,dict) or set(args)!={'action'} or args['action'] not in actions:
            raise ValueError('Only fixed current-worker operations allowed')
        c=self.c
        if args['action']=='health':
            return {'worker':'offline' if c.rpc.failure else 'ready','busy':c.busy,
                    'unknown':c.unknown,'generation':self.supervisor.state['generation'],
                    'recovery':self.supervisor.state['phase']}
        if args['action']=='reload_skills':
            result=await c.rpc.call('skills/list',{'cwds':[str(c.workspace)],'forceReload':True})
            return {'count':sum(len(r.get('skills',[])) for r in result['data']),
                    'errors':sum(len(r.get('errors',[])) for r in result['data'])}
        if args['action']=='reconnect_tools':
            await c.rpc.call('config/mcpServer/reload',{})
            return {'state':'reconnect_requested','login':'verify existing status; do not repeat uncertain external writes'}
        return self.supervisor.request({'action':'restart'})
