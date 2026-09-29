TIER_WEIGHTS: dict[str, float] = {
    "S": 5.0,
    "A": 4.0,
    "B": 3.0,
    "C": 2.0,
    "D": 1.0,
    "F": 0.5,
}

MOVIE_CANDIDATES_CACHE_KEY = "recommendations:movies:candidates"
GAME_CANDIDATES_CACHE_KEY = "recommendations:games:candidates"
CANDIDATES_CACHE_TTL = 60 * 60 * 26  # 26h

RECOMMENDATION_MARGIN = 0.5
MIN_VOTE_COUNT_TMDB = 1000
MIN_RATINGS_COUNT_RAWG = 500
CANDIDATE_POOL_SIZE = 100
MAX_CANDIDATE_PAGES = 20
RECOMMENDATIONS_LIMIT = 20
