import asyncio
import base64
import json
import websockets
import os
from dotenv import load_dotenv
from pharmacy_functions import FUNCTION_MAP
load_dotenv()

def sts_connect():
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise Exception("DEEPGRAM_API_KEY not found in environment variables.")
    #allows communicating with deepgram
    sts_ws = websockets.connect(
        "wss://agent.deepgram.com/v1/agent/converse",
        subprotocols=["token", api_key]
    )
    #multi part communication with deepgram which sends your voice to twilliow and it willl respond to us
    return sts_ws



def load_config():
    with open("config.json", "r") as f:
        config = json.load(f)
    return config

#handelling the three way commmunication between deepgram twilio and (us)
async def handle_barge_in(decoded, twilio_ws, streamsid):
    if decoded["type"] == "UserStartedSpeaking":
        clear_message = {
            "event": "clear",
            "streamSid": streamsid
        }
        await twilio_ws.send(json.dumps(clear_message))
        
        
        
def execute_function_call(func_name, arguments):  
    if func_name in FUNCTION_MAP:
        result =  FUNCTION_MAP[func_name](**arguments)
        print(f"Function call result: {result}")
        return result
    else:
        result = {"error": f"Function '{func_name}' not found"}
        print(result["error"]) 
        return result  
    
    
def create_function_call_response(func_id, func_name, result):
    return {
        "type": "FunctionCallResponse",
        "id": func_id,
        "name": func_name,
        "content": json.dumps(result)
    }
    
    
        #interupting the model 
async def handle_function_call_request(decoded, sts_ws):
    
    try:
        for function_call in decoded ["functions"]:
            func_name = function_call["name"]
            func_id = function_call["id"]
            arguments = json.loads(function_call["arguments"])
            
            
            print(f"Function call : {func_name}(ID: {func_id}) with arguments {arguments}")
            
            result = execute_function_call(func_name, arguments)    
            
                
            function_result = create_function_call_response(func_id, func_name, result)
            
            await sts_ws.send(json.dumps(function_result))
            print(f"Sent function result: {function_result}")
            
    except Exception as e:
        print(f"Error handling function call request: {e}")
        error_result  = create_function_call_response(
            func_id if "func_id" in locals() else "unknown",
            func_name if "func_name" in locals() else "unknown",
            {"error": f"Function call failed: {str(e)}"}
        )
        await sts_ws.send(json.dumps(error_result))

async def handle_text_message(decoded, twilio_ws,sts_ws,  streamsid):
    await handle_barge_in(decoded, twilio_ws, streamsid)
    
    if decoded["type"] == "FunctionCallRequest":
        await handle_function_call_request(decoded,sts_ws)


async def sts_sender(sts_ws, audio_queue):
    print("Started STS sender.")
    while True:
        chunk = await audio_queue.get()
        await sts_ws.send(chunk)#sending it to deepgram
        
        

async def sts_receiver(sts_ws, twilio_ws, streamsid_queue):
    print("Started STS receiver.")
    streamsid = await streamsid_queue.get()
    
    async for message in sts_ws:#websocket connection to deepgram
        if type(message) is str:

            print(message)
            decoded = json.loads(message)
            await handle_text_message(decoded, twilio_ws, sts_ws, streamsid)
            continue  
        
           
        raw_mulaw = message
        
        media_message = {
            "event": "media",
            "streamSid": streamsid,
            "media": {
                "payload": base64.b64encode(raw_mulaw).decode("ascii"),#DECODING TO NORMAL ENGLISH CHARACTERS
            }
        }
        await twilio_ws.send(json.dumps(media_message))   
        
        
        
            
async def twilio_receiver(twilio_ws, audio_queue, streamsid_queue):
    BUFFER_SIZE = 20 * 160  # 20ms of audio at 16kHz, 16-bit mono
    #dealing with bytes here because loading through  twillio we wil get in bytes
    inbuffer = bytearray(b"")
    
    async for message in twilio_ws:
        try:
            data = json.loads(message)
            event = data ["event"]
            
            if event == "start":
                print("get our streamsid")
                start = data["start"]
                streamsid = start["streamSid"]
                streamsid_queue.put_nowait(streamsid)
                
            elif event == "connected":
                continue            
            elif event == "media":
                media = data["media"]
                chunk =base64.b64decode(media["payload"])#decoding the media payload from base64 to bytes
                if media["track"] == "inbound":
                    inbuffer.extend(chunk)
                    
            elif event == "stop":     
                break
            
            while len(inbuffer) >= BUFFER_SIZE:
                chunk = inbuffer[:BUFFER_SIZE]#certain amount is send as a buffer size so that to minimilaize latency
                audio_queue.put_nowait(chunk)
                inbuffer = inbuffer[BUFFER_SIZE:]
        
        except:
            break
                
                
                
                
async def twilio_handler(twilio_ws):
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()

    async with sts_connect() as sts_ws:
        config_message = load_config()
        await sts_ws.send(json.dumps(config_message))
        
        await asyncio.wait(
            [   
                asyncio.ensure_future(sts_sender(sts_ws, audio_queue)),
                asyncio.ensure_future(sts_receiver(sts_ws, twilio_ws, streamsid_queue)),
                asyncio.ensure_future(twilio_receiver(twilio_ws, audio_queue, streamsid_queue))
            ]
        )
        
        await twilio_ws.close()


async def main():
    await websockets.serve(twilio_handler, "localhost", 5000)
    print("Started server.")
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())