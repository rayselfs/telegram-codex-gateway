"""Small installed-capability boundary; handlers never select their actor."""
import inspect
import re
from types import MappingProxyType

TOOL={'type':'function','name':'gateway_capability',
      'description':'Use an installed deployment capability. Identity and request scope are bound by the gateway. External writes still require their specific authorization.',
      'inputSchema':{'type':'object','properties':{'capability':{'type':'string'},'action':{'type':'string'},'arguments':{'type':'object'}},
                     'required':['capability','action','arguments'],'additionalProperties':False}}

class Capabilities:
    def __init__(self,members):
        self.members=members;self.handlers={}

    def register(self,identifier,handler,actions):
        if not re.fullmatch('[a-z][a-z0-9_]{0,63}',identifier) or identifier in self.handlers:
            raise ValueError('Invalid or duplicate installed capability')
        if not actions or any(role not in ('active','admin') for role in actions.values()):
            raise ValueError('Explicit action scopes required')
        self.handlers[identifier]=(handler,dict(actions))

    def authorize(self,owner,identifier,action):
        self.members.require_active(owner)
        if identifier not in self.handlers:raise PermissionError('Capability is not installed')
        _,actions=self.handlers[identifier]
        if action not in actions:raise PermissionError('Capability action is not registered')
        if actions[action]=='admin':self.members.require_admin(owner)

    async def dispatch(self,owner,identifier,action,arguments,request_id):
        self.authorize(owner,identifier,action)
        if not isinstance(arguments,dict) or any(k in arguments for k in ('owner_id','actor_id','request_id','code_root','data_root')):
            raise ValueError('Actor, request and deployment cannot be supplied by content')
        if not isinstance(request_id,str) or not request_id:raise ValueError('Bound request required')
        context=MappingProxyType({'owner_id':owner,'capability':identifier,'action':action,'request_id':request_id})
        self.authorize(owner,identifier,action)
        result=self.handlers[identifier][0](context,arguments)
        return await result if inspect.isawaitable(result) else result

class CapabilityTools:
    def __init__(self,registry,owner):self.registry,self.owner=registry,owner

    async def apply(self,args,request_id):
        if not isinstance(args,dict) or set(args)!={'capability','action','arguments'}:
            raise ValueError('Only capability, action and arguments are accepted')
        return await self.registry.dispatch(self.owner,args['capability'],args['action'],args['arguments'],request_id)
