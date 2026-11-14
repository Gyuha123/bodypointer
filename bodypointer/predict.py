"""단어 자동완성: 초성 검색, 자모 단위 앞부분 일치, 다음 단어 예측."""
import math
import time
from collections import Counter

from .hangul import choseong, decompose, is_consonant

# 처음 쓰는 사람을 위한 기본 단어 (사용할수록 개인 단어가 앞으로 온다)
SEED_WORDS = """안녕하세요 감사합니다 고맙습니다 네 아니요 괜찮아요 좋아요 싫어요 잠깐만요 기다려주세요
도와주세요 화장실 물 밥 배고파요 목말라요 아파요 머리가 배가 졸려요 피곤해요 쉬고 싶어요 추워요 더워요
엄마 아빠 선생님 간호사 의사 병원 약 지금 오늘 내일 어제 나중에 빨리 천천히 다시 한번 조금 많이
사랑해요 보고 싶어요 미안해요 죄송합니다 알겠습니다 모르겠어요 그래요 맞아요 좋은 하루 되세요
전화 문자 메일 카톡 유튜브 검색 음악 영화 사진 확인 부탁드립니다 수고하셨습니다""".split()


class Predictor:
    def __init__(self):
        self.counts: Counter = Counter()      # 단어 → 사용 횟수
        self.recent: dict = {}                # 단어 → 마지막 사용 시각
        self.bigrams: Counter = Counter()     # (앞 단어, 다음 단어) → 횟수
        for w in SEED_WORDS:
            self.counts[w] += 0               # 키만 등록 (점수는 아래에서 기본값 부여)

    def load(self, words: dict, bigrams: dict, recent: dict | None = None):
        for w, c in words.items():
            self.counts[w] += c
        for k, c in bigrams.items():
            self.bigrams[k] += c
        self.recent.update(recent or {})

    def learn(self, word: str, prev: str | None = None):
        word = word.strip()
        if not word or len(word) > 30:
            return
        self.counts[word] += 1
        self.recent[word] = time.time()
        if prev:
            self.bigrams[(prev, word)] += 1

    def _score(self, w: str) -> float:
        c = self.counts.get(w, 0)
        age_days = (time.time() - self.recent[w]) / 86400 if w in self.recent else 30
        return (c + 0.3) * (1.0 + 1.0 / (1.0 + age_days)) + (0 if c else -0.1 * len(w) / 10)

    def suggest(self, query: str, prev: str | None = None, k: int = 5) -> list[str]:
        """query: 지금 치고 있는 단어(조합 중 글자 포함). 비어 있으면 다음 단어를 예측한다."""
        if not query:
            if not prev:
                return []
            nxt = [(w2, c) for (w1, w2), c in self.bigrams.items() if w1 == prev]
            nxt.sort(key=lambda x: -x[1])
            return [w for w, _ in nxt[:k]]

        if all(is_consonant(ch) for ch in query):
            # 초성 검색: 'ㅇㄴㅎ' → 안녕하세요
            cands = [w for w in self.counts if choseong(w).startswith(query) and w != query]
        else:
            q = decompose(query)
            cands = [w for w in self.counts if w != query and decompose(w).startswith(q)]
        bonus = {w2: c for (w1, w2), c in self.bigrams.items() if prev and w1 == prev}
        cands.sort(key=lambda w: -(self._score(w) + 2 * math.log1p(bonus.get(w, 0))))
        return cands[:k]
