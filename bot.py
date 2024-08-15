import asyncio
import json
import logging
import os

import requests
from ShazamAPI import Shazam
from dotenv import load_dotenv
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)

load_dotenv()
# Load configuration
bot_token = os.getenv('BOT_API_TOKEN')
spotify_client_id = os.getenv('SPOTIFY_CLIENT_ID')
spotify_client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')

# Initialize bot and Spotify
bot = AsyncTeleBot(bot_token)
spotify_credentials = SpotifyClientCredentials(client_id=spotify_client_id, client_secret=spotify_client_secret)
spotify = Spotify(client_credentials_manager=spotify_credentials)

# File to store user and track information
data_file = 'data.json'

class FindMusic:
    @staticmethod
    async def get_track_info(tracks):
        if isinstance(tracks, dict):
            track_name = tracks['name']
            artist = tracks['artists'][0]['name']
            song_page = tracks['external_urls']['spotify']
            response = f"Track: {track_name}\nArtist: {artist}\nLink: {song_page}"
            return response
        else:
            raise ValueError("Expected 'track' to be a dictionary")

    @staticmethod
    async def search_track(query):
        search_results = spotify.search(q=query, type='track', limit=10)
        tracks = search_results['tracks']['items']
        if not tracks:
            return None
        return tracks

    @staticmethod
    def load_data():
        if os.path.exists(data_file):
            with open(data_file, 'r') as file:
                return json.load(file)
        return {}

    @staticmethod
    def save_data(data):
        with open(data_file, 'w') as file:
            json.dump(data, file, indent=4)


class Keyboard:
    def __init__(self):
        self.current_index = 0
        self.query = None
        self.tracks = None

    @staticmethod
    def _create_keyboard():
        keyboard = InlineKeyboardMarkup()
        keyboard.add(
            InlineKeyboardButton('Previous', callback_data='previous'),
            InlineKeyboardButton('Next', callback_data='next'),
            InlineKeyboardButton('Back', callback_data='back')
        )
        return keyboard

    async def _handle_next(self, call):
        self.current_index += 1
        if self.current_index >= len(self.tracks):
            self.current_index = len(self.tracks) - 1
            await bot.answer_callback_query(call.id, text='No more tracks available.')
            return
        await self._edit_track_info(call)

    async def _handle_previous(self, call):
        self.current_index -= 1
        if self.current_index < 0:
            self.current_index = 0
            await bot.answer_callback_query(call.id, text='No previous tracks available.')
            return
        await self._edit_track_info(call)

    @staticmethod
    async def _handle_back(call):
        await bot.delete_message(call.message.chat.id, call.message.message_id)
        await bot.delete_message(call.message.chat.id, call.message.message_id-1)

    async def _edit_track_info(self, call):
        track_info = await FindMusic.get_track_info(self.tracks[self.current_index])
        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=track_info,
            reply_markup=self._create_keyboard()
        )


class ConvertMusic(Keyboard):
    def __init__(self):
        super().__init__()
        self.user_data = FindMusic.load_data()  # Load user data when initializing the class

    @staticmethod
    async def convertor(qname, getting_file, content_type):
        local_voice_file = f"{qname}.ogg" if content_type == 'voice' else f"{qname}.mp4"
        output_mp3 = f"{qname}.mp3"

        with open(local_voice_file, 'wb') as file:
            file.write(getting_file.content)

        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-i', local_voice_file, '-ar', '44100', '-ac', '2', '-b:a', '192k', output_mp3,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        os.remove(local_voice_file)

        return output_mp3

    async def convert(self, message):
        self.query = None
        self.current_index = 0

        get_file = await bot.get_file(message.voice.file_id) if message.voice else await bot.get_file(
            message.video.file_id)
        path = get_file.file_path
        qname = os.path.basename(path)
        getting_file = requests.get(f'https://api.telegram.org/file/bot{bot_token}/{path}', stream=True)
        output_mp3 = await self.convertor(qname, getting_file, message.content_type)

        with open(output_mp3, 'rb') as mp3_file:
            shazam = Shazam(mp3_file.read())
            recognize_generator = shazam.recognizeSong()
            os.remove(output_mp3)

            try:
                find_sh = next(recognize_generator)
                self.query = find_sh[1].get('track').get('title')
            except (AttributeError, StopIteration):
                self.query = None

        if self.query is not None:
            self.tracks = await FindMusic().search_track(self.query)
            if not self.tracks:
                await bot.send_message(message.chat.id, "No tracks found")
                return

            track_info = await FindMusic.get_track_info(self.tracks[self.current_index])
            await bot.send_message(message.chat.id, track_info, reply_markup=self._create_keyboard())
        else:
            await bot.send_message(message.chat.id, "Could not identify the song")

        # Update user data
        self.user_data[str(message.chat.id)] = {
            'query': self.query,
            'tracks': [await FindMusic.get_track_info(track) for track in (self.tracks or [])]
        }
        FindMusic.save_data(self.user_data)

    async def handle_callback_query(self, call):
        logging.info(f"Received callback query: {call.data}")
        if call.data == 'next':
            await self._handle_next(call)
        elif call.data == 'previous':
            await self._handle_previous(call)
        elif call.data == 'back':
            await self._handle_back(call)
        else:
            await bot.answer_callback_query(call.id, text='Unknown action')



# Create an instance of ConvertMusic
convert_music = ConvertMusic()

# Register message and callback query handlers with the bot
@bot.message_handler(content_types=['voice', 'video'])
async def handle_message(message):
    await convert_music.convert(message)


@bot.callback_query_handler(func=lambda call: True)
async def handle_callback_query(call):
    await convert_music.handle_callback_query(call)


@bot.message_handler(commands=['start'])
async def start(message):
    await bot.send_message(message.chat.id,
                           "Hi! I'm Hitori! Your personal music finder. "
                           "Give me a song name, and I'll find it for you.")


# Start polling (keep this at the end of the script)
async def main():
    await bot.polling()


if __name__ == "__main__":
    asyncio.run(main())
