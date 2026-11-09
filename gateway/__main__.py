"""Run the Gateway CLI and its background entry points."""
import runpy
import sys


def main():
    commands = {
        'init': 'gateway.core.init',
        'background': 'gateway.core.background',
        'guard': 'gateway.core.gateway_guard',
        'worker': 'gateway.scheduling.schedule_worker',
        'release': 'gateway.core.release',
    }
    command = sys.argv[1] if len(sys.argv) > 1 else None
    module = commands.get(command)
    if module:
        del sys.argv[1]
    runpy.run_module(module or 'gateway.core.main', run_name='__main__')


if __name__ == '__main__':
    main()
