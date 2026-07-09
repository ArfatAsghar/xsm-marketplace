import os
import re
import requests

def extract_handle_or_id(url_or_text):
    """Parses a YouTube URL or text input to extract the channel handle, name, or ID."""
    url_or_text = url_or_text.strip()
    if not url_or_text:
        return None
    
    # Check if input is a full URL
    if "youtube.com" in url_or_text:
        # Match handles like /@handle
        match_handle = re.search(r"youtube\.com/(@[a-zA-Z0-9_\-\.]+)", url_or_text)
        if match_handle:
            return match_handle.group(1)
        
        # Match channel IDs like /channel/UCxxxxxx
        match_id = re.search(r"youtube\.com/channel/(UC[a-zA-Z0-9_\-]+)", url_or_text)
        if match_id:
            return match_id.group(1)
        
        # Match user links like /c/username or /user/username
        match_user = re.search(r"youtube\.com/(?:c|user)/([a-zA-Z0-9_\-]+)", url_or_text)
        if match_user:
            return match_user.group(1)
    
    # Return as is (could be a handle @name, raw ID UCxxx, or name)
    return url_or_text

def resolve_channel_id(handle_or_name, api_key):
    """Resolves a channel name, username handle (@handle) or user URL to a canonical channel ID."""
    if not api_key:
        return None
        
    handle_or_name = handle_or_name.strip()
    if handle_or_name.startswith("UC") and len(handle_or_name) == 24:
        return handle_or_name # Already a channel ID
        
    try:
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "type": "channel",
            "q": handle_or_name,
            "maxResults": 1,
            "key": api_key
        }
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                return items[0]["id"]["channelId"]
    except Exception as e:
        print(f"[FETCH ERROR] Failed to resolve channel ID: {e}")
    return None

def fetch_channel_stats(channel_id, api_key):
    """Fetches base channel stats: title, creation date, sub count, total views, video count, etc."""
    if not api_key:
        return None
        
    try:
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {
            "part": "statistics,snippet",
            "id": channel_id,
            "key": api_key
        }
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                item = items[0]
                stats = item.get("statistics", {})
                snippet = item.get("snippet", {})
                return {
                    "id": channel_id,
                    "title": snippet.get("title", "Unknown"),
                    "handle": snippet.get("customUrl", ""),
                    "description": snippet.get("description", ""),
                    "published_at": snippet.get("publishedAt", ""),
                    "country": snippet.get("country", "US"),
                    "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                    "subscriber_count": int(stats.get("subscriberCount", 0) or 0),
                    "hidden_subscriber_count": bool(stats.get("hiddenSubscriberCount", False)),
                    "total_views": int(stats.get("viewCount", 0) or 0),
                    "video_count": int(stats.get("videoCount", 0) or 0)
                }
    except Exception as e:
        print(f"[FETCH ERROR] Failed to fetch channel stats: {e}")
    return None

def fetch_recent_videos(channel_id, api_key, max_results=30):
    """Fetches video IDs of the most recent uploads from the channel."""
    if not api_key:
        return []
        
    try:
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "id",
            "channelId": channel_id,
            "maxResults": max_results,
            "order": "date",
            "type": "video",
            "key": api_key
        }
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            items = r.json().get("items", [])
            return [item["id"]["videoId"] for item in items if "videoId" in item.get("id", {})]
    except Exception as e:
        print(f"[FETCH ERROR] Failed to fetch recent video IDs: {e}")
    return []

def fetch_video_details(video_ids, api_key):
    """Fetches engagement metrics (views, likes, comments) and settings for a list of video IDs."""
    if not api_key or not video_ids:
        return []
        
    try:
        url = "https://www.googleapis.com/youtube/v3/videos"
        # Split into chunks of 50 (API limit)
        video_details = []
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i+50]
            params = {
                "part": "statistics,status,snippet",
                "id": ",".join(chunk),
                "key": api_key
            }
            r = requests.get(url, params=params, timeout=5)
            if r.status_code == 200:
                items = r.json().get("items", [])
                for item in items:
                    v_stats = item.get("statistics", {})
                    v_snippet = item.get("snippet", {})
                    
                    # Comments can be disabled, which shows up as commentCount missing or throwing error
                    comments_disabled = False
                    try:
                        comment_count = int(v_stats.get("commentCount", 0) or 0)
                    except (ValueError, TypeError):
                        comment_count = 0
                        comments_disabled = True
                        
                    video_details.append({
                        "id": item.get("id"),
                        "title": v_snippet.get("title", ""),
                        "views": int(v_stats.get("viewCount", 0) or 0),
                        "likes": int(v_stats.get("likeCount", 0) or 0),
                        "comments": comment_count,
                        "comments_disabled": comments_disabled
                    })
        return video_details
    except Exception as e:
        print(f"[FETCH ERROR] Failed to fetch video details: {e}")
    return []

def fetch_recent_comments(channel_id, api_key, max_results=50):
    """Fetches raw comments from the channel's public videos to run NLP & diversity checks."""
    if not api_key:
        return []
        
    try:
        url = "https://www.googleapis.com/youtube/v3/commentThreads"
        params = {
            "part": "snippet",
            "allThreadsRelatedToChannelId": channel_id,
            "maxResults": max_results,
            "textFormat": "plainText",
            "key": api_key
        }
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            items = r.json().get("items", [])
            comments = []
            for item in items:
                snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                text = snippet.get("textDisplay", "")
                if text:
                    comments.append(text)
            return comments
    except Exception as e:
        print(f"[FETCH ERROR] Failed to fetch recent comments: {e}")
    return []

def fetch_all_data(channel_url_or_handle, api_key=None):
    """
    Orchestrates the entire fetching sequence.
    Returns: a dict of raw stats or None if channel not found.
    """
    if not api_key:
        api_key = os.getenv("YOUTUBE_API_KEY") or "AIzaSyANYgZeJUfdPI9jJPpcJjvgr5BgoLfY254"
        
    parsed = extract_handle_or_id(channel_url_or_handle)
    if not parsed:
        return None
        
    # If no API key is set, we return mock/simulated channel data so the app can be tested fully.
    if not api_key:
        print("[YOUTUBE DETECTOR] API Key missing. Generating simulated YouTube statistics.")
        return generate_mock_youtube_data(parsed)
        
    # Resolve to channel ID
    channel_id = resolve_channel_id(parsed, api_key)
    if not channel_id:
        # Fallback search as handle directly
        channel_id = resolve_channel_id(f"@{parsed.lstrip('@')}", api_key)
        
    if not channel_id:
        return None
        
    # Ingest stats
    ch_stats = fetch_channel_stats(channel_id, api_key)
    if not ch_stats:
        return None
        
    video_ids = fetch_recent_videos(channel_id, api_key, max_results=30)
    video_details = fetch_video_details(video_ids, api_key)
    comments = fetch_recent_comments(channel_id, api_key, max_results=50)
    
    return {
        "channel": ch_stats,
        "videos": video_details,
        "comments": comments
    }

def generate_mock_youtube_data(handle_or_name):
    """Deterministic mock generator to test the app without an API key."""
    import hashlib
    import random
    
    clean_handle = handle_or_name.lower().replace("@", "")
    seed = int(hashlib.md5(clean_handle.encode()).hexdigest(), 16) % 1000000
    rng = random.Random(seed)
    
    is_bot = (seed % 10) < 3 # 30% chance of generating a bot-profile
    
    # Base numbers
    subscribers = rng.randint(1000, 500000)
    video_count = rng.randint(20, 300)
    age_days = rng.randint(30, 2000)
    
    # Standard lists
    organic_phrases = ["great video!", "wow very nice", "love this content", "subscribed!", "keep it up"]
    spam_phrases = ["free cash in bio!", "check my site", "earn $1000/day", "click here", "sub to my channel!"]
    
    videos = []
    num_videos = 30
    
    if not is_bot:
        # Organic distributions
        total_views = subscribers * rng.uniform(20, 150)
        mean_views = subscribers * rng.uniform(0.05, 0.3)
        for i in range(num_videos):
            views = int(rng.lognormvariate(math_log(mean_views), 0.4))
            views = max(100, views)
            likes = int(views * rng.uniform(0.02, 0.09))
            comments = int(views * rng.uniform(0.001, 0.01))
            videos.append({
                "id": f"vid_{i}",
                "title": f"Organic Video {i}",
                "views": views,
                "likes": likes,
                "comments": comments,
                "comments_disabled": False
            })
        comments_list = [rng.choice(organic_phrases) if rng.random() > 0.05 else rng.choice(spam_phrases) for _ in range(50)]
    else:
        # Bot profiles
        bot_type = rng.choice(["dead_subscribers", "fake_engagement", "flat_views"])
        total_views = subscribers * rng.uniform(0.1, 5.0)
        
        if bot_type == "dead_subscribers":
            # High sub count, extremely low views
            mean_views = subscribers * rng.uniform(0.0001, 0.001)
            for i in range(num_videos):
                views = int(rng.uniform(mean_views * 0.5, mean_views * 1.5))
                views = max(5, views)
                likes = int(views * rng.uniform(0.01, 0.03))
                comments = rng.randint(0, 2)
                videos.append({
                    "id": f"vid_{i}",
                    "title": f"Low View Video {i}",
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "comments_disabled": False
                })
            comments_list = [rng.choice(organic_phrases) for _ in range(5)]
            
        elif bot_type == "fake_engagement":
            # High views, near-zero likes/comments or high spam comments
            mean_views = subscribers * rng.uniform(0.8, 3.0)
            for i in range(num_videos):
                views = int(rng.uniform(mean_views * 0.8, mean_views * 1.2))
                likes = int(views * rng.uniform(0.0001, 0.003)) # extremely low like ratio
                comments = int(views * rng.uniform(0.00001, 0.0005))
                videos.append({
                    "id": f"vid_{i}",
                    "title": f"Hyped Video {i}",
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "comments_disabled": False
                })
            comments_list = [rng.choice(spam_phrases) if rng.random() > 0.4 else rng.choice(organic_phrases) for _ in range(50)]
            
        else: # flat_views
            # views standard deviation is virtually zero (exact purchase package)
            fixed_views = int(subscribers * rng.uniform(0.1, 0.15))
            for i in range(num_videos):
                views = int(rng.normalvariate(fixed_views, fixed_views * 0.005))
                likes = int(views * 0.04) # rigid likes ratio
                comments = int(views * 0.002)
                videos.append({
                    "id": f"vid_{i}",
                    "title": f"Uniform Video {i}",
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "comments_disabled": False
                })
            comments_list = [rng.choice(spam_phrases) if rng.random() > 0.5 else rng.choice(organic_phrases) for _ in range(40)]
            
    return {
        "channel": {
            "id": f"UC_mock_{seed}",
            "title": f"Mock Channel {handle_or_name.capitalize()}",
            "handle": f"@{clean_handle}",
            "description": "This is a mock channel created for testing.",
            "published_at": "2020-01-01T00:00:00Z",
            "country": "US",
            "thumbnail": "",
            "subscriber_count": subscribers,
            "hidden_subscriber_count": False,
            "total_views": int(total_views),
            "video_count": video_count
        },
        "videos": videos,
        "comments": comments_list
    }

def math_log(val):
    import math
    return math.log(max(1, val))
