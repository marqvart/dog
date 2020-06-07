#!/usr/bin/env python3

import argparse
import configparser
import os
import pprint
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Dict, Union

REGISTRY = 'gitlab.kitenet.com:4567'
CONFIG_FILE = 'dog.config'
VERSION = 4

DogConfig = Dict[str, Union[str, int, bool, List[str], Dict[str, str]]]


def log_verbose(config: DogConfig, txt: str):
    if config['verbose']:
        print(txt)


def log_config(name: str, config: DogConfig, filename: Path = None):
    if filename:
        print('{} Config ({}):'.format(name, filename))
    else:
        print('{} Config:'.format(name))
    pprint.pprint(config, indent=4)


def default_config() -> DogConfig:
    return {'sudo-outside-docker': False,
            'exposed-dog-variables': ['uid', 'gid', 'user', 'group', 'home', 'as-root'],
            'uid': 1000,
            'gid': 1000,
            'user': 'nobody',
            'group': 'nogroup',
            'home': '/home/nobody',
            'p4user': 'nobody',
            'cwd': '/home/nobody',
            'auto-mount': True,
            'volumes': {},
            'args': ['id'],
            'image': 'alpine',
            'registry': REGISTRY,
            'interactive': True,
            'terminal': False,
            'as-root': False,
            'pull': False,
            'verbose': False,
            'ports': {}}


def find_dog_config() -> Optional[Path]:
    cur = Path.cwd() / CONFIG_FILE
    for parent in cur.parents:
        dog_config = parent / CONFIG_FILE
        if dog_config.is_file():
            return dog_config
    return None


def read_dog_config(dog_config=find_dog_config()) -> DogConfig:
    if dog_config is None:
        image = 'esw/serverscripts/forge'
        return {'image': image, 'registry': REGISTRY}
    else:
        config = configparser.ConfigParser(delimiters='=')
        config.read(str(dog_config))
        dog_config = dict(config['dog'])
        # Parse booleans - use default_config() to determine which values should be booleans
        for k, v in default_config().items():
            if type(v) == bool and k in config['dog']:
                dog_config[k] = config['dog'].getboolean(k)
        if 'ports' in config:
            dog_config['ports'] = dict(config['ports'])
        if 'volumes' in config:
            dog_config['volumes'] = dict(config['volumes'])
        return dog_config


def parse_command_line_args() -> DogConfig:
    parser = argparse.ArgumentParser(description='Docker run wrapper to make it easier to call commands.')
    parser.add_argument('args', type=str, nargs='+', help='Command to call inside docker (with arguments)')
    parser.add_argument('--pull', dest='pull', action='store_const', const=True, help='Pull the latest version of the docker image')
    interactive_group = parser.add_mutually_exclusive_group()
    interactive_group.add_argument('--interactive', dest='interactive', action='store_const', const=True, help='Run interactive (keep stdin open)')
    interactive_group.add_argument('--not-interactive', dest='interactive', action='store_const', const=False, help='Do not run interactive')
    terminal_group = parser.add_mutually_exclusive_group()
    terminal_group.add_argument('--terminal', dest='terminal', action='store_const', const=True, help='Allocate a pseudo terminal')
    terminal_group.add_argument('--no-terminal', dest='terminal', action='store_const', const=False, help='Do not allocate a pseudo terminal')
    parser.add_argument('--as-root', dest='as-root', action='store_const', const=True, help='Run as root inside the docker')
    parser.add_argument('--version', action='version', version='dog version {}'.format(VERSION))
    parser.add_argument('--verbose', dest='verbose', action='store_const', const=True, help='Provide more dog output (useful for debugging)')

    # Insert the needed -- to seperate dog args with the rest of the commands
    # But only if the user did not do it himself
    argv = list(sys.argv[1:])
    own_name = os.path.basename(sys.argv[0])
    if 'dog' not in own_name:
        argv.insert(0, own_name)
    if '--' not in argv:
        first_normal_arg = -1
        for index, arg in enumerate(argv):
            if arg[0] != '-':
                argv.insert(index, '--')
                break
    args = parser.parse_args(argv)
    config = vars(args)
    if config['pull'] is None:
        del config['pull']
    if config['terminal'] is None:
        del config['terminal']
    if config['interactive'] is None:
        del config['interactive']
    if config['as-root'] is None:
        del config['as-root']
    if config['verbose'] is None:
        del config['verbose']
    return config


def get_env_config() -> DogConfig:
    config = {}
    if sys.platform == 'win32':
        config['uid'] = 1000
        config['gid'] = 1000
        config['user'] = os.getenv('USERNAME')
        config['group'] = 'oticon'
        config['home'] = '/home/' + config["user"]
        # Write a unix version of the p4tickets.txt file
        win_version = Path.home() / 'p4tickets.txt'
        unix_version = Path.home() / 'dog_p4tickets.txt'
        if win_version.exists():
            # Convert windows line endings to unix line endings
            unix_version.write_bytes(win_version.read_text().encode('ascii'))
        else:
            unix_version.write_text('')

    else:
        import grp
        config['uid'] = os.getuid()
        config['gid'] = os.getgid()
        config['home'] = os.getenv('HOME')
        config['user'] = os.getenv('USER')
        config['group'] = grp.getgrgid(config['gid']).gr_name

    config['p4user'] = os.getenv('P4USER', config['user'])

    if sys.platform == 'win32':
        cwd = os.getcwd()
        config['win32-cwd'] = cwd
        config['cwd'] = '/' + cwd[0] + '/' + cwd.replace('\\', '/')[2:]
    else:
        config['cwd'] = os.getcwd()

    if sys.platform == 'win32':
        config['volumes'] = {str(Path.home() / "dog_p4tickets.txt"): config["home"] + '/.p4tickets:ro'}
    else:
        config['volumes'] = {str(Path.home() / ".p4tickets"): config["home"] + '/.p4tickets:ro'}

    config['volumes'][str(Path.home() / ".ssh")] = config["home"] + '/.ssh:ro'

    return config


def find_mount_point(p: Path):
    while not p.is_mount():
        p = p.parent
    return p


def run(config: DogConfig):
    assert config['full-image'] is not None, 'You need to at least specify the full_image.'

    if config['auto-mount']:
        if sys.platform == 'win32':
            drive = Path(config['win32-cwd']).drive
            config['volumes'][drive + '\\'] = '/' + drive[0]
        else:
            mount_point = find_mount_point(Path(config['cwd']))
            config['volumes'][mount_point] = mount_point

    args = []
    if config['sudo-outside-docker']:
        args += ['sudo']
    args += ['docker']
    args += ['run',
             '--rm',
             '--hostname=kbnuxdockerrtol',
             '-w', config['cwd']]

    for outside, inside in config['volumes'].items():
        args += ['-v', outside + ':' + inside]

    for outside, inside in config['ports'].items():
        args += ['-p', outside + ':' + inside]

    if config['interactive']:
        args.append('-i')

    if config['terminal']:
        args.append('-t')

    if not config['as-root']:
        args.extend(['-e', 'USER=' + config["user"],
                     '-e', 'P4USER=' + config["p4user"]])

    for name in config['exposed-dog-variables']:
        env_name = name.upper().replace('-', '_')
        args.extend(['-e', 'DOG_{}={}'.format(env_name, config[name])])

    args.append(config['full-image'])
    args.extend(config['args'])

    log_verbose(config, ' '.join(args))
    try:
        proc = subprocess.run(args)
        return proc.returncode
    except KeyboardInterrupt:
        print('Dog received Ctrl+C')
        return -1


def update_config(existing_config: DogConfig, new_config: DogConfig):
    for k, v in new_config.items():
        if k in existing_config and type(v) == dict:
            update_config(existing_config[k], new_config[k])
        else:
            existing_config[k] = new_config[k]


def main() -> int:
    default_conf = default_config()
    env_config = get_env_config()
    user_config_file = Path.home() / '.dog.config'
    user_config = None
    if user_config_file.is_file():
        user_config = read_dog_config(user_config_file)
    dog_config = read_dog_config()
    command_line_config = parse_command_line_args()

    config = {}
    update_config(config, default_conf)
    update_config(config, env_config)
    if user_config:
        update_config(config, user_config)
    update_config(config, dog_config)
    update_config(config, command_line_config)

    if config['verbose']:
        log_config('Default', default_conf)
        log_config('Environment', env_config)
        log_config('User', user_config, user_config_file)
        log_config('Dog', dog_config, find_dog_config())
        log_config('Final', config)

    if 'full-image' not in config:
        config['full-image'] = config['registry'] + '/' + config['image']

    if config['pull']:
        try:
            args = []
            if config['sudo-outside-docker']:
                args += ['sudo']
            args += ['docker']
            args += ['pull', config['full-image']]
            proc = subprocess.run(args)
            if proc.returncode != 0:
                print('ERROR {} while pulling:'.format(proc.returncode))
                print(proc.stdout)
                print(proc.stderr)
                sys.exit(proc.returncode)
        except KeyboardInterrupt:
            print('Dog received Ctrl+C')
            return -1

    return run(config)


if __name__ == '__main__':
    sys.exit(main())
