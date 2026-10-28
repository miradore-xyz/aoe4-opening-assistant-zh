"""在线音色扩充回归：参数白名单、旧缓存兼容及后台准备；不联网、不播放。"""
import asyncio
import os
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from online_voice import CHINESE_VOICES, DEFAULT_VOICE, OnlineSpeech

EXPECTED = {
    'zh-CN-XiaoxiaoNeural', 'zh-CN-XiaoyiNeural', 'zh-CN-YunjianNeural', 'zh-CN-YunxiNeural',
    'zh-CN-YunxiaNeural', 'zh-CN-YunyangNeural', 'zh-CN-liaoning-XiaobeiNeural', 'zh-CN-shaanxi-XiaoniNeural',
    'zh-HK-HiuGaaiNeural', 'zh-HK-HiuMaanNeural', 'zh-HK-WanLungNeural',
    'zh-TW-HsiaoChenNeural', 'zh-TW-YunJheNeural', 'zh-TW-HsiaoYuNeural',
}
TEXT = '保持村民生产，提前建造房屋。'


class CacheOnly:
    """仅调用缓存路径计算，不创建播放器或后台线程。"""
    _parameters = staticmethod(OnlineSpeech._parameters)
    _text = staticmethod(OnlineSpeech._text)
    cache_path = OnlineSpeech.cache_path

    def __init__(self, directory):
        self.cache_dir = Path(directory)


class VoiceParameterTests(unittest.TestCase):
    def test_verified_roster_and_old_default_remain(self):
        self.assertEqual({voice for voice, _ in CHINESE_VOICES}, EXPECTED)
        self.assertEqual(len(CHINESE_VOICES), 14)
        self.assertEqual(DEFAULT_VOICE, 'zh-CN-XiaoxiaoNeural')
        self.assertEqual([voice for voice, _ in CHINESE_VOICES[:3]],
                         ['zh-CN-XiaoxiaoNeural', 'zh-CN-YunxiNeural', 'zh-CN-XiaoyiNeural'])

    def test_all_new_ids_are_allowed_and_captions_identify_language_and_gender(self):
        counts = {'普通话': 0, '台湾国语': 0, '粤语': 0, '东北话': 0, '陕西方言': 0}
        for voice, caption in CHINESE_VOICES:
            self.assertEqual(OnlineSpeech._parameters(voice, 0), (voice, 0))
            self.assertTrue(caption.endswith(('女声', '男声')))
            matches = [label for label in counts if label in caption]
            self.assertEqual(len(matches), 1)
            counts[matches[0]] += 1
        self.assertEqual(counts, {'普通话': 6, '台湾国语': 3, '粤语': 3, '东北话': 1, '陕西方言': 1})

    def test_invalid_voice_message_is_generic_and_existing_rate_limits_remain(self):
        with self.assertRaisesRegex(ValueError, '列表中的中文在线音色'):
            OnlineSpeech._parameters('zh-CN-NotListedNeural', 0)
        for rate in (-11, 11):
            with self.assertRaises(ValueError):
                OnlineSpeech._parameters(DEFAULT_VOICE, rate)

    def test_three_old_cache_filenames_are_byte_compatible(self):
        # 扩充前按原缓存协议记录的固定短句文件名，不依赖用户缓存或当前实现重算期望。
        before = {
            'zh-CN-XiaoxiaoNeural': '16f35fcd320895b8b47b3b5b81748c9493aa11c261275b9cadcc70b85dbcbacc.mp3',
            'zh-CN-YunxiNeural': '7c89b71b111e7535faaedc89c69005571c3f7d08a1394cf4321e0f0a52c4a8cd.mp3',
            'zh-CN-XiaoyiNeural': '4e9803a95997d2511c606da96508d02bd108bcfb17fbe3c6281165468f48499b.mp3',
        }
        cache = CacheOnly('隔离缓存')
        for voice, name in before.items():
            self.assertEqual(cache.cache_path(TEXT, voice, 0).name, name)

    def test_different_voices_and_rates_never_share_the_same_cache_key(self):
        cache = CacheOnly('隔离缓存')
        paths = {cache.cache_path(TEXT, voice, rate) for voice, _ in CHINESE_VOICES for rate in (-2, 0, 2)}
        self.assertEqual(len(paths), 42)


class VoicePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='aoe4-voice-test-')
        self.voice = OnlineSpeech(Path(self.directory.name) / '隔离缓存')
        self.completed, self.errors = [], []
        self.voice.prepared.connect(self.completed.append)
        self.voice.problem.connect(self.errors.append)

    def wait_for(self, predicate):
        until = time.monotonic() + 4
        while time.monotonic() < until:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('后台语音准备没有按时返回。')

    def tearDown(self):
        self.voice.close()
        self.wait_for(lambda: not self.voice._thread.is_alive())
        QTest.qWait(20)
        self.voice.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.directory.cleanup()

    def test_new_mainland_taiwan_and_cantonese_ids_prepare_and_reuse_independent_caches(self):
        calls = []

        async def download(_self, text, chosen, rate, path, _job):
            calls.append((text, chosen, rate))
            await asyncio.sleep(.005)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\xff\xfb' + b'0' * 200)  # 仅模拟网络提交；实际MP3另做在线帧校验。

        self.voice._download_audio = types.MethodType(download, self.voice)
        for chosen in ('zh-CN-YunjianNeural', 'zh-TW-YunJheNeural', 'zh-HK-WanLungNeural'):
            job = self.voice.prepare(['持续生产村民', '持续生产村民', '提前补房'], chosen)
            self.wait_for(lambda: job in self.completed)
            self.assertTrue(self.voice.ready_for(['持续生产村民', '提前补房'], chosen))
            repeated = self.voice.prepare(['持续生产村民', '提前补房'], chosen)
            self.wait_for(lambda: repeated in self.completed)
        self.assertEqual(len(calls), 6)
        self.assertEqual({chosen for _, chosen, _ in calls},
                         {'zh-CN-YunjianNeural', 'zh-TW-YunJheNeural', 'zh-HK-WanLungNeural'})
        self.assertFalse(self.voice.ready_for(['持续生产村民'], DEFAULT_VOICE))

    def test_replaced_job_does_not_mark_old_voice_ready(self):
        entered = threading.Event()

        async def download(_self, text, _chosen, _rate, path, _job):
            if text == '旧任务':
                entered.set()
                await asyncio.sleep(30)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\xff\xfb' + b'0' * 200)

        self.voice._download_audio = types.MethodType(download, self.voice)
        old = self.voice.prepare(['旧任务'], 'zh-CN-YunjianNeural')
        self.wait_for(entered.is_set)
        latest = self.voice.prepare(['新任务'], 'zh-TW-HsiaoYuNeural')
        self.wait_for(lambda: latest in self.completed)
        self.assertNotIn(old, self.completed)
        self.assertFalse(self.voice.ready_for(['旧任务'], 'zh-CN-YunjianNeural'))
        self.assertTrue(self.voice.ready_for(['新任务'], 'zh-TW-HsiaoYuNeural'))

    def test_failed_synthesis_never_marks_voice_ready_or_plays_missing_audio(self):
        async def download(_self, text, _chosen, _rate, _path, _job):
            if text == '超时':
                raise asyncio.TimeoutError()
            raise RuntimeError('模拟服务失败')

        self.voice._download_audio = types.MethodType(download, self.voice)
        for text, message in (('超时', '准备超时'), ('失败', '准备失败')):
            before = len(self.errors)
            job = self.voice.prepare([text], 'zh-HK-HiuGaaiNeural')
            self.wait_for(lambda: len(self.errors) > before)
            self.assertIn(message, self.errors[-1])
            self.assertNotIn(job, self.completed)
            self.assertFalse(self.voice.ready_for([text], 'zh-HK-HiuGaaiNeural'))
        self.assertFalse(self.voice.say('未缓存短句', 'zh-HK-HiuGaaiNeural'))
        self.assertIn('尚未缓存', self.errors[-1])


if __name__ == '__main__':
    unittest.main()
