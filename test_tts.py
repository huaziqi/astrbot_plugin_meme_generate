import asyncio
import edge_tts

async def test():

    text = "你好，我是 AstrBot"

    tts = edge_tts.Communicate(
        text=text,
        voice="zh-CN-XiaoxiaoNeural"
    )

    await tts.save("test.mp3")

asyncio.run(test())