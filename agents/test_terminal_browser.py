import asyncio
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import TerminalAccessGrant, TerminalSession, AuditEvent
from .test_terminal_transport import fixture
from .test_conpty_sessions import enable_conpty
from .terminal_test_harness import LoopbackASGI
from . import terminal_transport as transport


IDENTITY = {'sid':'S-1-5-21-1-2-3-1001','windows_session_id':1,'integrity':8192,'elevated':False}


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True,
    NIGHTOWL_TERMINAL_CONPTY_ENABLED=True, NIGHTOWL_TERMINAL_BROWSER_ENABLED=True)
class BrowserConsentControlTests(TestCase):
    def setUp(self):
        self.machine, token, self.operator = fixture('browser-control')
        enable_conpty(self.machine,self.operator)
        self.agent=Client(HTTP_AUTHORIZATION='Bearer '+token)
        self.client.force_login(self.operator)

    def create(self):
        response=self.client.post(reverse('terminal-session-request',args=[self.machine.pk]),
            {'type':'request','protocol':1,'mode':'conpty'},content_type='application/json',secure=True)
        self.assertEqual(response.status_code,201)
        self.info=response.json()
        return self.info

    def consent(self, decision='approved', identity=IDENTITY, client=None):
        return (client or self.agent).post(reverse('terminal-agent-consent',args=[self.info['session_id']]),
            {'machine_id':self.machine.machine_id,'decision':decision,'identity':identity},content_type='application/json',secure=True)

    def test_no_tickets_until_consent_then_one_use_issue(self):
        info=self.create()
        self.assertEqual(info['status'],'awaiting_consent')
        self.assertNotIn('credential',info)
        poll=self.agent.post(reverse('terminal-agent-poll'),{'machine_id':self.machine.machine_id},content_type='application/json',secure=True).json()
        self.assertIsNone(poll['session'])
        self.assertEqual(poll['consent_request']['operator_name'],self.operator.username)
        url=reverse('terminal-session-status',args=[info['session_id']])
        self.assertNotIn('credential',self.client.get(url,secure=True).json())
        self.assertEqual(self.consent().status_code,200)
        self.assertNotIn('credential',self.client.get(url,secure=True).json())
        connect=reverse('terminal-session-connect',args=[info['session_id']])
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.operator)
        self.assertEqual(csrf.post(connect,{'type':'connect','protocol':1},content_type='application/json',secure=True).status_code,403)
        status=self.client.post(connect,{'type':'connect','protocol':1},content_type='application/json',secure=True).json()
        self.assertIn('credential',status)
        self.assertEqual(status['identity'],IDENTITY)
        self.assertNotIn('credential',self.client.get(url,secure=True).json())
        self.assertEqual(self.client.post(connect,{'type':'connect','protocol':1},content_type='application/json',secure=True).status_code,409)
        self.assertEqual(self.consent().status_code,409)

    def test_agent_first_connection_can_still_mint_operator_ticket(self):
        self.create();self.assertEqual(self.consent().status_code,200)
        # A valid agent commonly reaches the transport before the browser status poll.
        TerminalSession.objects.filter(pk=self.info['session_id']).update(status='connecting',agent_ticket_used=True)
        response=self.client.post(reverse('terminal-session-connect',args=[self.info['session_id']]),
            {'type':'connect','protocol':1},content_type='application/json',secure=True)
        self.assertEqual(response.status_code,200)
        self.assertIn('credential',response.json())

    def test_denied_and_expired_never_mint_tickets(self):
        self.create(); self.assertEqual(self.consent('denied',None).status_code,200)
        session=TerminalSession.objects.get(pk=self.info['session_id'])
        self.assertEqual(session.status,'cancelled'); self.assertFalse(session.agent_ticket_hash)
        self.create()
        TerminalSession.objects.filter(pk=self.info['session_id']).update(consent_deadline=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.consent().json()['status'],'expired')
        self.assertFalse(TerminalSession.objects.get(pk=self.info['session_id']).operator_ticket_hash)

    def test_operator_and_other_endpoint_cannot_approve(self):
        self.create()
        self.assertIn(self.consent(client=self.client).status_code,(401,403))
        other_machine, token, _=fixture('foreign')
        self.assertEqual(Client(HTTP_AUTHORIZATION='Bearer '+token).post(reverse('terminal-agent-consent',args=[self.info['session_id']]),
            {'machine_id':other_machine.machine_id,'decision':'approved','identity':IDENTITY},content_type='application/json',secure=True).status_code,404)
        other=get_user_model().objects.create_user('foreign-reader',is_staff=True)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('terminal-session-status',args=[self.info['session_id']]),secure=True).status_code,404)

    def test_system_elevated_changed_authorization_and_beacon_cancel(self):
        self.create()
        for identity in ({**IDENTITY,'sid':'S-1-5-18'},{**IDENTITY,'elevated':True},{**IDENTITY,'integrity':12288},{**IDENTITY,'windows_session_id':True}):
            self.assertEqual(self.consent(identity=identity).status_code,409)
        TerminalAccessGrant.objects.filter(operator=self.operator).update(allow_conpty=False)
        self.assertEqual(self.consent().status_code,409)
        response=self.client.post(reverse('terminal-session-cancel',args=[self.info['session_id']]),{'type':'cancel'},secure=True)
        self.assertEqual(response.status_code,200)
        self.assertEqual(TerminalSession.objects.get(pk=self.info['session_id']).status,'cancelled')

    def test_ui_gate_and_no_remote_executable_scripts(self):
        url=reverse('endpoint-detail',args=[self.machine.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-terminal-panel')
        self.assertContains(response,'vendor/xterm/xterm.js')
        self.assertNotContains(response,'https://unpkg.com/lucide')
        with override_settings(NIGHTOWL_TERMINAL_BROWSER_ENABLED=False):
            self.assertNotContains(self.client.get(url),'data-terminal-panel')


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True,NIGHTOWL_TERMINAL_SINGLE_PROCESS=True,NIGHTOWL_TERMINAL_CONPTY_ENABLED=True,
    NIGHTOWL_TERMINAL_BROWSER_ENABLED=True,NIGHTOWL_TERMINAL_TEST_LOOPBACK=True,ALLOWED_HOSTS=['127.0.0.1','testserver'],
    SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False)
class TerminalBrowserSmokeTests(TransactionTestCase):
    async def test_real_browser_real_pty_simulated_local_consent_click(self):
        # Fixtures/identity deliberately isolated, never a corporate endpoint.
        from asgiref.sync import sync_to_async
        def setup():
            machine,token,operator=fixture('browser-real-smoke')
            enable_conpty(machine,operator)
            client=Client();client.force_login(operator)
            return machine,token,client.cookies['sessionid'].value
        machine,token,cookie=await sync_to_async(setup)()
        root=Path(settings.BASE_DIR)
        dll=root/'NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll'
        self.assertTrue(dll.exists())
        with tempfile.TemporaryDirectory(prefix='nightowl-browser-agent-') as home:
            async with LoopbackASGI() as server:
                transport.HUB=transport.TerminalHub()
                agent=await asyncio.create_subprocess_exec('dotnet',str(dll),'--terminal-browser-agent',
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env={**os.environ,
                    'NIGHTOWL_HOME':home,'NIGHTOWL_TEST_SERVER':server.origin,'NIGHTOWL_TEST_MACHINE':machine.machine_id,'NIGHTOWL_TEST_TOKEN':token})
                browser=await asyncio.create_subprocess_exec('node',str(root/'tests/frontend/test_terminal_browser.cjs'),
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env={**os.environ,
                    'NIGHTOWL_TEST_SERVER':server.origin,'NIGHTOWL_TEST_COOKIE':cookie,'NIGHTOWL_TEST_ENDPOINT_PATH':reverse('endpoint-detail',args=[machine.pk])})
                try:
                    stdout,stderr=await asyncio.wait_for(browser.communicate(),110)
                    for line in stdout.decode().splitlines():
                        if line.startswith(('BROWSER_TERMINAL_MEASURED=','TERMINAL_BROWSER_FAILURE=','TERMINAL_BROWSER_STATE=','TERMINAL_BROWSER_EVAL_ERROR=')):print(line)
                    if browser.returncode:
                        state=await sync_to_async(lambda:list(TerminalSession.objects.filter(endpoint=machine).values('status','reason','consent_at','operator_ticket_used','agent_ticket_used')))()
                        print('TERMINAL_BROWSER_DB_STATE='+json.dumps(state,default=str))
                        if agent.returncode is not None:
                            output,error=await agent.communicate()
                            print('TERMINAL_AGENT_EARLY_EXIT='+json.dumps({'code':agent.returncode,'error_type':error.decode().split(':',1)[0][:100]}))
                    self.assertEqual(browser.returncode,0,'Browser smoke failed; process content withheld')
                    output,error=await asyncio.wait_for(agent.communicate(),15)
                    for line in output.decode().splitlines():
                        if line.startswith(('BROWSER_AGENT_RESOURCE=','CONSENT_TEST_MODE=')):print(line)
                    self.assertEqual(agent.returncode,0,'Native agent smoke failed; process content withheld')
                    sessions=await sync_to_async(lambda:list(TerminalSession.objects.filter(endpoint=machine)))()
                    self.assertEqual(len(sessions),2)
                    self.assertTrue(all(session.status in ('closed','cancelled') for session in sessions))
                    event=await sync_to_async(lambda:AuditEvent.objects.filter(event_type='terminal.ready',endpoint=machine).latest('created_at'))()
                    self.assertIn('consent_to_ready_ms',event.metadata)
                    print('BROWSER_CONSENT_TO_READY='+json.dumps(event.metadata))
                finally:
                    for process in (browser,agent):
                        if process.returncode is None: process.kill();await process.wait()
