"""Offline checks for isolated-test admission. Never connects to Docker or PostgreSQL."""
import os
from unittest import TestCase, mock
from types import SimpleNamespace
import json
import sys

import rag386660_isolated_pg as isolated


class AdmissionTests(TestCase):
    def env(self):
        return dict(RAG386660_IT_RUN_ID='a'*16,RAG386660_IT_PORT='55442',
                    RAG386660_IT_SYSTEM_ID='12345678901234567890',RAG386660_IT_SYNTHETIC_PASSWORD='b'*64)

    def test_default_does_not_connect(self):
        with mock.patch.object(sys,'argv',['isolated']),mock.patch.object(isolated,'connect') as connect, \
             mock.patch.object(isolated,'verify_container') as verify:
            self.assertEqual(isolated.main(),0)
        connect.assert_not_called(); verify.assert_not_called()

    def test_no_fallback_to_business_database_or_port(self):
        for field,value in (('RAG386660_IT_RUN_ID',''),('RAG386660_IT_PORT','5432'),
                            ('RAG386660_IT_PORT','55441'),('RAG386660_IT_SYSTEM_ID',''),
                            ('RAG386660_IT_SYNTHETIC_PASSWORD','')):
            env=self.env(); env[field]=value
            with self.subTest(field=field,value=value), mock.patch.dict(os.environ,env,clear=True), self.assertRaises(isolated.Stop):
                isolated.configuration()

    def test_valid_configuration_uses_unique_database(self):
        with mock.patch.dict(os.environ,self.env(),clear=True):
            config=isolated.configuration()
        self.assertEqual(config['database'],'rag386660_it_'+'a'*16)
        self.assertNotEqual(config['database'],'reviews')

    def test_container_label_and_no_bind_mount_are_mandatory(self):
        with mock.patch.dict(os.environ,self.env(),clear=True): config=isolated.configuration()
        item={'Name':'/rag386660-it-'+'a'*16,'State':{'Running':True},
              'Config':{'Image':'pgvector/pgvector:pg18','Labels':{'rag386660.run_id':'a'*16}},
              'NetworkSettings':{'Ports':{'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':'55442'}]}},'Mounts':[]}
        good=SimpleNamespace(returncode=0,stdout=json.dumps([item]))
        probe=SimpleNamespace(returncode=0,stdout=config['system_id']+'\n')
        context=SimpleNamespace(returncode=0,stdout=json.dumps([{'Endpoints':{'docker':{'Host':'npipe:////./pipe/docker_engine'}}}]))
        with mock.patch.dict(os.environ,{},clear=True), mock.patch.object(isolated.subprocess,'run',side_effect=[context,good,probe]):
            isolated.verify_container(config)
        item['Mounts']=[{'Type':'bind'}]
        bad=SimpleNamespace(returncode=0,stdout=json.dumps([item]))
        with mock.patch.dict(os.environ,{},clear=True), mock.patch.object(isolated.subprocess,'run',side_effect=[context,bad]), self.assertRaises(isolated.Stop):
            isolated.verify_container(config)
