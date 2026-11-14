"""Python network boundary. Native-code/OS isolation is deliberately NOT certified."""
import socket
import sys
from pathlib import Path


def socketpair_call():
    frame=sys._getframe(1)
    while frame:
        if (Path(frame.f_code.co_filename).resolve()==Path(socket.__file__).resolve()
            and frame.f_code.co_name in ('_fallback_via_loopback','_fallback_socketpair','socketpair')):
            return True
        frame=frame.f_back
    return False


def audit(event,args,*,child=False):
    if not event.startswith('socket.'):
        return
    if child:
        raise RuntimeError('f02_child_network_forbidden')
    if event=='socket.__new__':
        if args[1] not in (socket.AF_INET,socket.AF_INET6) or args[2] & 0xf != socket.SOCK_STREAM:
            raise RuntimeError('f02_non_tcp_socket_forbidden')
    elif event in ('socket.sendto','socket.sendmsg','socket.gethostbyname','socket.gethostbyaddr',
                    'socket.getnameinfo','socket.sethostname'):
        raise RuntimeError('f02_udp_dns_forbidden')
    elif event=='socket.getaddrinfo':
        if args[0] not in ('127.0.0.1','::1') or str(args[1])!='5432':
            raise RuntimeError('f02_dns_forbidden')
    elif event=='socket.connect':
        address=args[1]
        if not (isinstance(address,tuple) and address[0] in ('127.0.0.1','::1')
                and (address[1]==5432 or socketpair_call())):
            raise RuntimeError('f02_tcp_destination_forbidden')
    elif event=='socket.bind':
        address=args[1]
        if not (isinstance(address,tuple) and address[0] in ('127.0.0.1','::1')
                and address[1] in (0,8011)):
            raise RuntimeError('f02_listener_forbidden')


def install_child():
    sys.addaudithook(lambda event,args:audit(event,args,child=True))


def isolation_status():
    # Python hooks do not constrain native DLL/Winsock/libpq calls. No OS policy
    # is installed by this repository; do not accept an unchecked JSON attestation.
    return dict(status='BLOCKED',python_tcp_udp_dns=True,
                os_network_isolation_verified=False,
                reason='No verified OS boundary for native-code networking; real entry remains disabled.')
