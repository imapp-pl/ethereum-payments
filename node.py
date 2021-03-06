import atexit
import json
import logging
import os
import time
from os import path

import psutil

from devp2p.crypto import privtopub
from ethereum.keys import privtoaddr
from ethereum.transactions import Transaction
from ethereum.utils import normalize_address

from golem.environments.utils import find_program
from golem.utils import find_free_net_port
from golem.core.simpleenv import _get_local_datadir

log = logging.getLogger('golem.ethereum')


class Faucet(object):
    PRIVKEY = "{:32}".format("Golem Faucet")
    assert len(PRIVKEY) == 32
    PUBKEY = privtopub(PRIVKEY)
    ADDR = privtoaddr(PRIVKEY)

    @staticmethod
    def gimme_money(ethnode, addr, value):
        nonce = ethnode.get_transaction_count(Faucet.ADDR.encode('hex'))
        addr = normalize_address(addr)
        tx = Transaction(nonce, 1, 21000, addr, value, '')
        tx.sign(Faucet.PRIVKEY)
        h = ethnode.send(tx)
        log.info("Faucet --({} ETH)--> {} ({})".format(float(value) / 10**18,
                                                       addr.encode('hex'), h))
        h = h[2:].decode('hex')
        assert h == tx.hash
        return h

    @staticmethod
    def deploy_contract(ethnode, init_code):
        nonce = ethnode.get_transaction_count(Faucet.ADDR.encode('hex'))
        tx = Transaction(nonce, 0, 3141592, to='', value=0, data=init_code)
        tx.sign(Faucet.PRIVKEY)
        ethnode.send(tx)
        return tx.creates


class NodeProcess(object):

    def __init__(self, nodes, datadir):
        if not path.exists(datadir):
            os.makedirs(datadir)
        assert path.isdir(datadir)
        if nodes:
            nodes_file = path.join(datadir, 'static-nodes.json')
            json.dump(nodes, open(nodes_file, 'w'))
        self.datadir = datadir
        self.__ps = None
        self.rpcport = None

    def is_running(self):
        return self.__ps is not None

    def start(self, rpc, mining=False, nodekey=None, port=None):
        if self.__ps:
            return

        assert not self.rpcport
        program = find_program('geth')
        assert program  # TODO: Replace with a nice exception
        # Data dir must be set the class user to allow multiple nodes running
        basedir = path.dirname(__file__)
        genesis_file = path.join(basedir, 'genesis_golem.json')
        if not port:
            port = find_free_net_port()
        self.port = port
        args = [
            program,
            '--datadir', self.datadir,
            '--networkid', '9',
            '--port', str(self.port),
            '--genesis', genesis_file,
            '--nodiscover',
            '--ipcdisable',  # Disable IPC transport - conflicts on Windows.
            '--gasprice', '0',
            '--verbosity', '3',
        ]

        if rpc:
            self.rpcport = find_free_net_port()
            args += [
                '--rpc',
                '--rpcport', str(self.rpcport)
            ]

        if nodekey:
            self.pubkey = privtopub(nodekey)
            args += [
                '--nodekeyhex', nodekey.encode('hex'),
            ]

        if mining:
            mining_script = path.join(basedir, 'mine_pending_transactions.js')
            args += [
                '--etherbase', Faucet.ADDR.encode('hex'),
                'js', mining_script,
            ]

        self.__ps = psutil.Popen(args)
        atexit.register(lambda: self.stop())
        WAIT_PERIOD = 0.01
        wait_time = 0
        while True:
            # FIXME: Add timeout limit, we don't want to loop here forever.
            time.sleep(WAIT_PERIOD)
            wait_time += WAIT_PERIOD
            if not self.rpcport:
                break
            if self.rpcport in set(c.laddr[1] for c
                                   in self.__ps.connections('tcp')):
                break
        log.info("Node started in {} s: `{}`".format(wait_time, " ".join(args)))

    def stop(self):
        if self.__ps:
            start_time = time.clock()
            self.__ps.terminate()
            self.__ps.wait()
            self.__ps = None
            self.rpcport = None
            duration = time.clock() - start_time
            log.info("Node terminated in {:.2f} s".format(duration))


# TODO: Refactor, use inheritance FullNode(NodeProcess)
class FullNode(object):
    def __init__(self, datadir=None):
        if not datadir:
            datadir = path.join(_get_local_datadir('ethereum'), 'full_node')
        self.proc = NodeProcess(nodes=[], datadir=datadir)
        self.proc.start(rpc=False, mining=True, nodekey=Faucet.PRIVKEY,
                        port=30900)

if __name__ == "__main__":
    import signal
    import sys

    logging.basicConfig(level=logging.INFO)
    FullNode()

    # The best I have to make the node running untill interrupted.
    def handler(*unused):
        sys.exit()
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    while True:
        time.sleep(60 * 60 * 24)
