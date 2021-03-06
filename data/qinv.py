#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""

Usage:
    qnib_.py [options]
    qnib_.py (-h | --help)
    qnib_.py --version

Options:
    --host <str>            neo4j hostname [default: neo4j.service.consul]

General Options:
    -h --help               Show this screen.
    --version               Show version.
    --loglevel, -L=<str>    Loglevel [default: INFO]
                            (ERROR, CRITICAL, WARN, INFO, DEBUG)
    --log2stdout, -l        Log to stdout, otherwise to logfile. [default: True]
    --logfile, -f=<path>    Logfile to log to (default: <scriptname>.log)
    --cfg, -c=<path>        Configuration file.

"""

# load librarys
import logging
import os
import re
import codecs
import ast
import sys
import json
from ConfigParser import RawConfigParser, NoOptionError
from osquery import osquery

### costum libraries
from neo4jrestclient.client import GraphDatabase, Node
from neo4jrestclient.query import QuerySequence

try:
    from docopt import docopt
except ImportError:
    HAVE_DOCOPT = False
else:
    HAVE_DOCOPT = True

__author__ = 'Christian Kniep <christian()qnib.org>'
__copyright__ = 'Copyright 2015 QNIB Solutions'
__license__ = """GPL v2 License (http://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html)"""


class QnibConfig(RawConfigParser):
    """ Class to abstract config and options
    """
    specials = {
        'TRUE': True,
        'FALSE': False,
        'NONE': None,
    }

    def __init__(self, opt):
        """ init """
        RawConfigParser.__init__(self)
        if opt is None:
            self._opt = {
                "--log2stdout": False,
                "--logfile": None,
                "--loglevel": "ERROR",
            }
        else:
            self._opt = opt
            self.logformat = '%(asctime)-15s %(levelname)-5s [%(module)s] %(message)s'
            self.loglevel = opt['--loglevel']
            self.log2stdout = opt['--log2stdout']
            if self.loglevel is None and opt.get('--cfg') is None:
                print "please specify loglevel (-L)"
                sys.exit(0)
            self.eval_cfg()

        self.eval_opt()
        self.set_logging()
        logging.info("SetUp of QnibConfig is done...")

    def do_get(self, section, key, default=None):
        """ Also lent from: https://github.com/jpmens/mqttwarn
            """
        try:
            val = self.get(section, key)
            if val.upper() in self.specials:
                return self.specials[val.upper()]
            return ast.literal_eval(val)
        except NoOptionError:
            return default
        except ValueError:  # e.g. %(xxx)s in string
            return val
        except:
            raise
            return val

    def config(self, section):
        ''' Convert a whole section's options (except the options specified
                explicitly below) into a dict, turning

                    [config:mqtt]
                    host = 'localhost'
                    username = None
                    list = [1, 'aaa', 'bbb', 4]

                into

                    {u'username': None, u'host': 'localhost', u'list': [1, 'aaa', 'bbb', 4]}

                Cannot use config.items() because I want each value to be
                retrieved with g() as above
            SOURCE: https://github.com/jpmens/mqttwarn
            '''

        d = None
        if self.has_section(section):
            d = dict((key, self.do_get(section, key))
                     for (key) in self.options(section) if key not in ['targets'])
        return d

    def eval_cfg(self):
        """ eval configuration which overrules the defaults
            """
        cfg_file = self._opt.get('--cfg')
        if cfg_file is not None:
            fd = codecs.open(cfg_file, 'r', encoding='utf-8')
            self.readfp(fd)
            fd.close()
            self.__dict__.update(self.config('defaults'))

    def eval_opt(self):
        """ Updates cfg according to options """

        def handle_logfile(val):
            """ transforms logfile argument
                """
            if val is None:
                logf = os.path.splitext(os.path.basename(__file__))[0]
                self.logfile = "%s.log" % logf.lower()
            else:
                self.logfile = val

        self._mapping = {
            '--logfile': lambda val: handle_logfile(val),
        }
        for key, val in self._opt.items():
            if key in self._mapping:
                if isinstance(self._mapping[key], str):
                    self.__dict__[self._mapping[key]] = val
                else:
                    self._mapping[key](val)
                break
            else:
                if val is None:
                    continue
                mat = re.match("\-\-(.*)", key)
                if mat:
                    self.__dict__[mat.group(1)] = val
                else:
                    logging.info("Could not find opt<>cfg mapping for '%s'" % key)

    def set_logging(self):
        """ sets the logging """
        self._logger = logging.getLogger()
        self._logger.setLevel(logging.DEBUG)
        if self.log2stdout:
            hdl = logging.StreamHandler()
            hdl.setLevel(self.loglevel)
            formatter = logging.Formatter(self.logformat)
            hdl.setFormatter(formatter)
            self._logger.addHandler(hdl)
        else:
            hdl = logging.FileHandler(self.logfile)
            hdl.setLevel(self.loglevel)
            formatter = logging.Formatter(self.logformat)
            hdl.setFormatter(formatter)
            self._logger.addHandler(hdl)

    def __str__(self):
        """ print human readble """
        ret = []
        for key, val in self.__dict__.items():
            if not re.match("_.*", key):
                ret.append("%-15s: %s" % (key, val))
        return "\n".join(ret)

    def __getitem__(self, item):
        """ return item from opt or __dict__
        :param item: key to lookup
        :return: value of key
        """
        if item in self.__dict__.keys():
            return self.__dict__[item]
        else:
            return self._opt[item]


class InventoryClass(object):
    """ Class to push local inventory into Neo4j
    """

    def __init__(self, cfg):
        """ Init of instance
        """
        self._cfg = cfg
        self.con_gdb()
        self._osq = osquery()

    def run(self):
        """ does sth
        """
        self.push_pkg()
        self.push_groups()
        self.push_users()
        self.push_logged_in_users()
        self.push_processes()

    def push_pkg(self):
        """ fetch installed system packages and push it to Neo4j
        """
        self._pkg = self._gdb.labels.create("Pkg")
        if os.path.exists("/etc/redhat-release"):
            self.push_rpm()
            self.push_rpm_files()


    def push_rpm(self):
        """ push rpm information
        """
        os_query = "SELECT name, version, arch FROM rpm_packages WHERE name in ('less', 'procps-ng','vim-enhanced', 'python-pip');"
        for item in json.loads(self._osq.setOutputMode("--json").query(os_query)):
            query = "MERGE (a:Arch {arch:{arch}}) MERGE (p:Pkg {name:{name}})-[:IS_ARCH]->a"
            query += " MERGE (i:Installation {version:{version}})"
            query += " MERGE (c:Latest)"
            query += " ON CREATE SET i.created_at = timestamp(), i.seen_at = timestamp()"
            query += " ON MATCH SET i.seen_at = timestamp() "
            query += " MERGE (i)-[:IS_VERSION]->p"
            query += " MERGE (i)-[:IS_ALIVE]->c"
            self._gdb.query(q=query, params=item)

    def push_rpm_files(self):
        """ push rpm file information
        """
        os_query ="SELECT package AS name, path, mode, size FROM rpm_package_files;"
        for item in json.loads(self._osq.setOutputMode("--json").query(os_query)):
            if item['mode'].startswith("07"):
                # TODO: Only add executables
                item['file_name'] = os.path.split(item['path'])[-1]
                item['size'] = int(item['size'])
                query = "MATCH (p:Pkg {name:{name}})"
                query += " MERGE (f:File {name:{file_name}, path:{path}, size:{size}, mode:{mode}})"
                query += " MERGE (f)<-[:PROVIDES]-(p) RETURN count(*)"
                res = self._gdb.query(q=query, params=item)
                #print item, res

    def push_processes(self):
        """ push process table
        """
        os_query = "SELECT pid, name, path, cmdline, uid FROM processes;"
        for item in json.loads(self._osq.setOutputMode("--json").query(os_query)):
            query = "MATCH (f:File {path:{path}})"
            query += " MERGE (p:Process {pid:{pid}, cmdline:{cmdline}, name:{name}, uid:{uid}})"
            query += " ON CREATE SET p.created_at = timestamp(), p.seen_at = timestamp()"
            query += " ON MATCH SET p.seen_at = timestamp()"
            query += " MERGE (f)<-[:RUNS]-(p) RETURN count(*)"
            res = self._gdb.query(q=query, params=item)
            #print item, res


    def push_users(self):
        """ push users information
        """
        self._users = self._gdb.labels.create("Users")
        query ="SELECT uid,gid,username,directory,shell FROM users;"
        for item in json.loads(self._osq.setOutputMode("--json").query(query)):
            query = "MATCH (g:Groups {gid:{gid}})"
            query += " MERGE (u:Users {name:{username},directory:{directory},shell:{shell},uid:{uid}, gid:{gid}})"
            query += " MERGE (u)-[:MEMBER]->(g)"
            self._gdb.query(query, params=item)


    def push_groups(self):
        """ push groups information
        """
        self._groups = self._gdb.labels.create("Groups")
        query ="SELECT gid,groupname AS name FROM groups;"
        for item in json.loads(self._osq.setOutputMode("--json").query(query)):
            new_node = self._gdb.nodes.create(**item)
            self._groups.add(new_node)


    def push_logged_in_users(self):
        """ push logged_in_users information
        """
        self._logged_in_users = self._gdb.labels.create("LoggedInUsers")
        query ="SELECT user AS name,host,time, tty,pid FROM logged_in_users;"
        for item in json.loads(self._osq.setOutputMode("--json").query(query)):
	    query = "MATCH (u:Users {name:{name}})"
	    query += " MERGE (l:LoggedInUsers {host:{host}, time:{time}, tty:{tty}, pid:{pid}})"
	    query += " MERGE (u)<-[:LOGIN]-(l)"
            new_relation = self._gdb.query(query, params=item)    


    def con_gdb(self):
        """ connect to neo4j
        """
        url = "http://%(--host)s:7474" % self._cfg
        try:
            self._gdb = GraphDatabase(url)
        except ConnectionError:
            time.sleep(3)
            self.con_gdb()


def main():
    """ main function """
    options = None
    if HAVE_DOCOPT:
        options = docopt(__doc__, version='Test Script 0.1')
    qcfg = QnibConfig(options)
    inv = InventoryClass(qcfg)
    inv.run()


if __name__ == "__main__":
    main()
