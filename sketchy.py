#!/usr/bin/env python3
import random
from datetime import datetime
from collections import OrderedDict

from flask import Flask, session, request, Response, render_template
from flask_session import Session
from flask_socketio import SocketIO, emit, rooms, join_room, leave_room
from eventlet import spawn_after

DICT_FILE = "/usr/share/dict/british-english"
RANDOM_NAMES_FILE = "animal_name_list.txt"
MAX_CHAT_QUEUE_LEN = 200

# Keep similar colours in threes and they'll render by each other.
COLOUR_PALETTE = OrderedDict([
    ("black", "#000000"), ("white", "#808080"), ("grey", "#ffffff"),
    ("maroon", "#800000"), ("red", "#e6194b"), ("pink", "#fabebe"),
    ("brown", "#aa6e28"), ("orange", "#f58231"), ("coral", "#ffd8b1"),
    ("olive", "#808000"), ("yellow", "#ffe119"), ("beige", "#fffac8"),
    ("green", "#3cb44b"), ("lime", "#d2f53c"), ("mint", "#aaffc3"),
    ("navy", "#000080"), ("blue", "#0082c8"), ("cyan", "#46f0f0"),
    ("purple", "#911eb4"), ("magenta", "#f032e6"), ("lavender", "#e6beff"),
])

app = Flask(__name__)
app.debug = False
app.secret_key = "zeppelin_rules"
app.config["SESSION_TYPE"] = "filesystem"
Session(app)
socketio = SocketIO(app, manage_session=False, engineio_logger=True)


class NameGenerator:

    def __init__(self, namefilepath):
        with open(namefilepath) as f:
            self.names = f.read().splitlines()

    def get_name(self, sessionid):
        idx = hash(sessionid)%len(self.names)
        return(self.names[idx])

namegen = NameGenerator(RANDOM_NAMES_FILE)


class Player:

    def __init__(self, name, sessionid):
        self.sessionid = sessionid
        self.name = name

    def send_chat(self, msg):
        emit("chat", msg, room=self.sessionid)

    def send_playerlist(self, playerlist):
        emit("playerlist", playerlist, room=self.sessionid)


class SketchPlayer(Player):

    def __init__(self, name, sessionid):
        super().__init__(name, sessionid)
        self.isdrawer = False
        self.wasdrawer = False

    def send_paint(self, paintdata):
        emit("paint", paintdata, room=self.sessionid)


class Game:

    def __init__(self):
        self.players = []
        self.isrunning = False
        self.chat_history = []

    def playerconnect(self, sessionid):
        print(sessionid)
        p = self.get_player_from_sessionid(sessionid)
        if not p:
            global namegen
            newname = namegen.get_name(session.sid)
            self.add_new_player(newname, sessionid)

    def add_new_player(self, name, sessionid):
        p = SketchPlayer(name, sessionid)
        self.players.append(p)
        self.send_playerlist_all()
        return p

    def playerdisconnect(self, sessionid):
        p = self.get_player_from_sessionid(sessionid)
        if p:
            self.remove_player(p)

    def remove_player(self, leaver):
        self.players.remove(leaver)
        self.send_playerlist_all()
        if leaver.isdrawer:
            self.isrunning = False
            for p in self.players:
                p.send("Drawmaster {} has abandoned us :-(. The word was {}.".format(leaver.name, self.wordtoguess))

    def get_player_from_sessionid(self, sessionid):
        try:
            player = next((p for p in self.players if p.sessionid==sessionid))
        except StopIteration:
            return None
        return player

    def recieve_chat(self, msg, sessionid):
        speaker = self.get_player_from_sessionid(sessionid)
        msg = "{}: {}".format(speaker.name, msg)
        self.chat_history.append(msg)
        self.send_chat_all(msg)

    def send_chat_all(self, msg):
        for player in self.players:
            player.send_chat(msg)

    def send_playerlist_all(self):
        playerlist = [p.name for p in self.players]
        #playerlist = {p.name: {"isdrawer": p.isdrawer} for p in self.players}
        for player in self.players:
            player.send_playerlist(playerlist)

    def get_chat_msgs(sessionid):
        recipient = get_player_from_sessionid(sessionid)
        msg = recipient.chatqueue.get()
        return msg


class SketchGame(Game):

    def __init__(self):
        super().__init__()
        self.canvas_history = []
        self.wordtoguess = ""

    def playerconnect(self, sessionid):
        super().playerconnect(sessionid)
        # Send current canvas state to connected player
        p = self.get_player_from_sessionid(sessionid)
        for paint in self.canvas_history:
            p.send_paint(paint)

    def recieve_chat(self, msg, sessionid):
        super().recieve_chat(msg, sessionid)
        if msg == "!start":
            self.start_game()
        speaker = self.get_player_from_sessionid(sessionid)
        if msg == self.wordtoguess and not speaker.isdrawer:
            sendmsg = "{} has guessed the word—{}!".format(speaker.name, self.wordtoguess)
            self.send_chat_all(sendmsg)

    def recieve_paint(self, paintdata, sessionid):
        if "clear" in paintdata:
            self.canvas_history = []
        else:
            self.canvas_history.append(paintdata)
        self.send_paint_all(paintdata)

    def send_paint_all(self, paintdata):
        for player in self.players:
            player.send_paint(paintdata)

    def start_game(self):
        if not self.isrunning:
            self.wordtoguess = random.choice(open(DICT_FILE).readlines()).rstrip()
            drawer = self.pick_next_drawer()
            self.send_chat_all("New game started! {} is the drawmaster.".format(drawer.name))
            drawer.send_chat("Your word: {}.".format(self.wordtoguess))
            self.isrunning = True
            spawn_after(3, emit, ["chat", "HI"])
#        else:
#            return SomeSortOfError

    def pick_next_drawer(self):
        try:
            newdrawer = next(p for p in self.players
                             if not p.isdrawer
                             and not p.wasdrawer)
            olddrawer = self.current_drawer()
            if olddrawer:
                olddrawer.isdrawer = False
            newdrawer.isdrawer = True
            return newdrawer
        except StopIteration:
            return None

    def current_drawer(self):
        try:
            return next(p for p in self.players if p.isdrawer)
        except StopIteration:
            return None


    def guess(self, player, word):
        if word == self.wordtoguess and not player.isdrawer:
            self.send_chat_all("{} has guessed the word: {}!".format(player.name, word))
            self.end_game()
            self.isrunning = False

game = SketchGame()

@app.route("/")
def mainpage():
    global game
    return render_template("index.html",
                           colour_palette=COLOUR_PALETTE,
                           players=[p.name for p in game.players]
                           )

@socketio.on("chat", namespace="/event")
def recieve_chat(rcv):
    global game
    game.recieve_chat(rcv["text"], session.sid)

@socketio.on("paint", namespace="/event")
def recieve_paint(rcv):
    global game
    game.recieve_paint(rcv, session.sid)

@socketio.on("connect", namespace="/event")
def connect():
    # In the global context, we've instantiated SocketIO with
    # manage_session=False, but for some reason, the default room
    # it joins is still the ID of the natively managed session.
    # Ignore this, and join a new room with the main page's
    # session ID instead (i.e. the one managed by flask_session).
    join_room(session.sid)
    global game
    game.playerconnect(session.sid)

@socketio.on("disconnect", namespace="/event")
def disconnect():
    leave_room(session.sid)
    global game
    game.playerdisconnect(session.sid)
    print("Client disconnected")



if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0")
