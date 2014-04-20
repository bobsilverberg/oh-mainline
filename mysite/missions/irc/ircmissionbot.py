from django.conf import settings
from mysite.missions.models import IrcMissionSession
from mysite.missions.base import view_helpers

from irc.bot import SingleServerIRCBot

TOPIC_ANSWER = '42'
TOPIC_PREFIX = "OpenHatch IRC mission channel || The question: What is the answer to life, the universe, and everything? || "


class IrcMissionBot(SingleServerIRCBot):
    # States in which a session can be
    STATE_SAID_HI = 1
    STATE_ANSWERED_TOPIC = 2

    def __init__(self):
        SingleServerIRCBot.__init__(self, [settings.IRC_MISSION_SERVER],
                                    settings.IRC_MISSIONBOT_NICK,
                                    settings.IRC_MISSIONBOT_REALNAME)
        self.connection.buffer_class.errors = 'replace'
        self.channel = settings.IRC_MISSION_CHANNEL
        self.active_sessions = {}

        self.ircobj.execute_every(5, self.check_for_registered_nicks)

    def check_for_registered_nicks(self):
        for nick in self.active_sessions:
            if self.active_sessions[nick]['registration_status'] != 'registered':
                    self.connection.privmsg('NickServ', 'INFO %s' % nick)

    def on_nicknameinuse(self, conn, event):
        conn.nick(conn.get_nickname() + '_')

    def on_welcome(self, conn, event):
        conn.join(self.channel)
        IrcMissionSession.objects.all().delete()

    def setup_session(self, nick, conn):
        # Someone has joined the channel.
        if nick != conn.get_nickname():
            password = view_helpers.make_password()
            IrcMissionSession(nick=nick, password=password).save()
            conn.privmsg(self.channel,
                         'Hello, %(nick)s! To start the mission, reply to me with the words : %(password)s'
                         % {'nick': nick, 'password': password})

    def destroy_session(self, nick):
        IrcMissionSession.objects.filter(nick=nick).delete()
        if nick in self.active_sessions:
            del self.active_sessions[nick]

    def on_join(self, conn, event):
        nick = event.source.split('!')[0]
        channel = event.target
        if channel == self.channel:
            if nick != conn.get_nickname():
                # Somebody joined.
                self.setup_session(nick, conn)
            else:
                # Our join completed.
                self.topic = TOPIC_PREFIX + 'Who will complete the mission next?'
                conn.topic(self.channel, self.topic)

    def on_topic(self, conn, event):
        nick = event.source.split('!')[0]
        new_topic = event.arguments[0]

        # Don't verify the topic change if we're the ones who did it.
        if nick != conn.get_nickname():
            if not new_topic.startswith(TOPIC_PREFIX):
                conn.topic(self.channel, self.topic)
                conn.privmsg(nick, 'Please try to preserve the beginning of the topic.')
            else:
                self.topic = new_topic

    def on_namreply(self, conn, event):
        # this sets up sessions for each user in the channel - I think
        channel = event.arguments[1]
        nicks = event.arguments[2].split()
        for nick in nicks:
            if nick[0] in '@+':
                nick = nick[1:]  # remove op/voice prefix
            self.setup_session(nick, conn)

    def on_nick(self, conn, event):
        old_nick = event.source.split('!')[0]
        new_nick = event.target
        self.active_sessions[new_nick] = self.active_sessions[old_nick]
        del self.active_sessions[old_nick]
        session = IrcMissionSession.objects.get(nick=old_nick, person__isnull=False)
        session.nick = new_nick
        session.save()

    def on_part(self, conn, event):
        nick = event.source.split('!')[0]
        channel = event.target
        if channel == self.channel:
            self.destroy_session(nick)

    def on_kick(self, conn, event):
        nick = event.arguments[0]
        channel = event.target
        if channel == self.channel:
            self.destroy_session(nick)

    def on_privmsg(self, conn, event):
        nick = event.source.split('!')[0]
        target = event.target
        msg = event.arguments[0]
        if target == conn.get_nickname():
            self.handle_private_message(nick, msg, conn)
        elif target == self.channel:
            self.handle_channel_message(nick, msg, conn)
    on_pubmsg = on_privmsg

    def on_privnotice(self, conn, event):
        # Check for registered nicks

        from_nick = event.source.split('!')[0]
        if from_nick == 'NickServ':
            msg = event.arguments[0]
            if 'Information on ' in msg:
                # start collecting messages
                self.nickserv_message_queue = [msg]
            elif len(self.nickserv_message_queue):
                self.nickserv_message_queue.append(msg)
            if 'End of Info' in msg:
                # process message queue
                self._check_queue_for_registered_nick()
                # clear out queue
                self.nickserv_message_queue = []

    def _check_queue_for_registered_nick(self):
        nick_found = False
        for nick in self.active_sessions:
            if self.active_sessions[nick]['registration_status'] != 'registered':
                for msg in self.nickserv_message_queue:
                    if 'Information on ' in msg and nick in msg:
                        nick_found = True
                    if 'Registered : ' in msg and nick_found:
                        self.active_sessions[nick]['registration_status'] = 'registered'
                        self.connection.privmsg(self.channel,
                                                'Congratulations, %s! You have successfully registered your nickname.'
                                                % nick)
                        return

    def handle_private_message(self, nick, msg, conn):
        pass

    def handle_channel_message(self, nick, msg, conn):
        mynick_lower = conn.get_nickname().lower()
        msg_lower = msg.lower()
        if mynick_lower in msg_lower:
            try:
                session = IrcMissionSession.objects.get(nick=nick, person__isnull=True)
                if session.password.lower() in msg_lower:
                    self.active_sessions[nick] = {'registration_status': ''}
                    conn.privmsg(self.channel,
                                 "Great work, %s! You are now ready to start the mission. Check the mission web page for your next instruction."
                                 % nick)
            except IrcMissionSession.DoesNotExist:
                self.setup_session(nick, conn)
                self.handle_channel_message(nick, msg, conn)
