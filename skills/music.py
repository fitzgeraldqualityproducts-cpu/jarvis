"""Spotify."""
from skills import tool


@tool("Play music on Spotify: a song, artist, album or one of the user's playlists (e.g. 'my gym playlist').",
      query="what to play, e.g. 'Dead Fresh by Lil Baby' or 'gym'",
      kind="track, artist, album or playlist (a 'my ... playlist' request is a playlist)")
def play_music(query: str, kind: str = "track"):
    import spotify_control as spotify
    kind = kind if kind in ("track", "artist", "album", "playlist") else "track"
    return spotify.play_query(query.removesuffix(" on spotify"), kind)


@tool("Pause, resume, skip or go back on Spotify.", action="pause, resume, next or previous")
def music_control(action: str):
    import spotify_control as spotify
    return {"pause": spotify.pause, "resume": spotify.play, "play": spotify.play, "next": spotify.next_track,
            "skip": spotify.next_track, "previous": spotify.previous_track,
            "back": spotify.previous_track}.get(action.lower().strip(), spotify.play)()


@tool("Set Spotify's volume (0-100), or change it by a step.", level="0-100", change="e.g. 15 or -15 instead of a level")
def music_volume(level: int = -1, change: int = 0):
    import spotify_control as spotify
    return spotify.change_volume(change) if change else spotify.set_volume(str(max(0, min(100, level))))


@tool("Add a song to the Spotify queue.", query="the song to queue")
def queue_song(query: str):
    import spotify_control as spotify
    return spotify.queue_query(query)


@tool("What song is playing right now.")
def now_playing():
    import spotify_control as spotify
    return spotify.now_playing()
