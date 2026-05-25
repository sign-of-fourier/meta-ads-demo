import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

from dotenv import load_dotenv
load_dotenv()
from embeddings.pipeline import embed_ad

COMPONENTS = [
    {"slot": "headline", "slot_index": 0, "value": "Meet Your Dog's New Best Friend"},
    {"slot": "primary_text", "slot_index": 0, "value": "Soft on the outside. Squeaky on the inside."},
    {"slot": "image", "slot_index": 0, "value": "https://scontent-ord5-3.xx.fbcdn.net/v/t45.1600-4/659056158_1375939654218715_8560514463374936910_n.png?stp=dst-jpg_tt6&_nc_cat=100&ccb=1-7&_nc_sid=d5bd00&_nc_ohc=g_4JtSWsbZAQ7kNvwGmp9o8&_nc_oc=AdoIUV5hkHT3-itTewZr-N0MeSyHSS36EA-96QprTa1Nc4GUgz6t5nxqsBvuRWV7WE8&_nc_zt=1&_nc_ht=scontent-ord5-3.xx&edm=AJcBmwoEAAAA&_nc_gid=SHOzhQx8IN9kwfsizj9Uig&_nc_tpa=Q5bMBQEMDdHWFAfAq7V4NfdSZWwN7i09_y0LSDXyEXY9ECGXxdx1OO59fE2LpdDJAmfCzQOUJsv6lK82Ow&oh=00_Af0REswRuZ8QLzhZoHsFI4jfILfYWLQyzp-n8GAEDVimDQ&oe=69F069D3"},
]

async def main():
    ok = await embed_ad(user_id=1, ad_id="test_ad", campaign_id="test_campaign", components=COMPONENTS)
    print("Result:", ok)

asyncio.run(main())
