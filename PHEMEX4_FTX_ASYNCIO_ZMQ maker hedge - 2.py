import asyncio
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from math import trunc
import aiohttp
import numpy as np
import websockets
import zmq
import zmq.asyncio
from ftx_client_class import FtxClient  # nodig voor ftx api, ivm order plaatsen

import ssl
import certifi

ssl_context = ssl.create_default_context(cafile=certifi.where())

import logging
# logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.DEBUG)    # dit heb ik toegevoegd om te loggen

import warnings

warnings.filterwarnings(action="ignore", category=DeprecationWarning)

asyncio.set_event_loop_policy(
    asyncio.WindowsSelectorEventLoopPolicy())  # python-3.8.0a4  --> to prevent an asyncio zmq error

# # PHEMEX BTC LINEAR/INVERSE :
# uBTCUSD = USD SETTLEMENT BTC CONTRACT (LINEAR)
# BTCUSD = BTC SETTLEMENT BTC CONTRACT (INVERSE)

coin = "DOGE"
future_market = f"{coin.upper()}USD"  # PERP ON PHEMEX
spot_market = f"{coin.upper()}/USD"  # SPOT ON FTX

# ------------ PHEMEX --------------------------------
api_key_phemex = os.environ.get("API_PHEMEX")
api_secret_phemex = os.environ.get("SECRET_PHEMEX")

normal_api = "https://vapi.phemex.com"  # = high_rate_api    "https://api.phemex.com"  = NORMAL API
test_api = "https://testnet-api.phemex.com"

normal_ws = "wss://vapi.phemex.com/ws/"  # snelle ws   "wss://phemex.com/ws/"   normale ws
high_rate_ws = "wss://vapi.phemex.com/ws/"
test_ws = "wss://testnet.phemex.com/ws/"

expire_time = int(time.time()) + 119
key = api_secret_phemex.encode('utf-8')
token = hmac.new(key, (api_key_phemex + str(expire_time)).encode('utf8'), digestmod=hashlib.sha256).hexdigest()
auth_msg = "{" + f'"method": "user.auth", "params": ["API", "{api_key_phemex}", "{token}", {str(expire_time)}], "id": 1234' + "}"
# -------------------------------------------------


# ------------ FTX --------------------------------
api_key_ftx = os.environ.get("API_FTX")
api_secret_ftx = os.environ.get("SECRET_FTX")
subaccount_name = "1"

ts = int(time.time() * 1000)
signature = hmac.new(api_secret_ftx.encode(), f'{ts}websocket_login'.encode(), 'sha256').hexdigest()
msg_ftx = \
    {
        'op': 'login',
        'args': {
            'key': api_key_ftx,
            'sign': signature,
            'time': ts,
            'subaccount': subaccount_name
        }
    }
msg_ftx = json.dumps(msg_ftx)
# -------------------------------------------------

ctx = zmq.asyncio.Context.instance()


async def websocket_phemex_ticker():  # was websocket_bybit_public
    socket = ctx.socket(zmq.PUSH)
    socket.connect("tcp://172.31.41.224:5557")
    print("Phemex ticker ZQN socket connected.")
    async with websockets.connect(normal_ws,
                                  ssl=ssl_context) as websocket:  # belangrijk! voorkomt ssl error op AWS!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        await websocket.send(auth_msg)
        sequence = int(time.time())
        await asyncio.sleep(1)
        sub_msg = f'{{"id": {str(sequence)}, "method": "orderbook.subscribe", "params":["{future_market}"]}}'
        # ^^ let op ik moest text accolades dubbel gebruiken anders werden ze geinterpreteerd als fstring accolades
        await websocket.send(sub_msg)
        async for message in websocket:
            try:
                message = json.loads(message)
                if "error" in message:
                    await asyncio.sleep(0.01)
                if "book" in message:
                    if message["type"] == "snapshot":
                        book_ask = message["book"]["asks"]
                        book_bid = message["book"]["bids"]
                        book_ask = {item[0]: item[1] for item in book_ask}
                        book_bid = {item[0]: item[1] for item in book_bid}
                        phemex_ask = list(book_ask.keys())[0]
                        phemex_bid = list(book_bid.keys())[0]

                    if message["type"] == "incremental":
                        update_ask = message["book"]["asks"]
                        update_bid = message["book"]["bids"]
                        if update_ask:
                            for item in update_ask:
                                book_ask[item[0]] = item[1]
                            book_ask = {key: val for key, val in book_ask.items() if val != 0}
                            phemex_ask = (sorted(book_ask.keys()))[0]
                        if update_bid:
                            for item in update_bid:
                                book_bid[item[0]] = item[1]
                            book_bid = {key: val for key, val in book_bid.items() if val != 0}
                            phemex_bid = (sorted(book_bid.keys()))[-1]
                    message = {"Type": "Ticker", "Exchange": "PHEMEX", "Bid": phemex_bid, "Ask": phemex_ask}
                    message = json.dumps(message)
                    await socket.send_string(message)
            except Exception as e:
                print(f"EXCEPT 1a: {e}")


async def websocket_phemex_orderstatus():  # was websocket_bybit_private
    socket = ctx.socket(zmq.PUSH)
    socket.connect("tcp://172.31.41.224:5557")
    print("Phemex orderstatus ZQN socket connected.")
    async with websockets.connect(normal_ws,
                                  ssl=ssl_context) as websocket:  # belangrijk! voorkomt ssl error op AWS!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        await websocket.send(auth_msg)
        sequence = int(time.time())
        await asyncio.sleep(1)
        sub_msg = f'{{"id": {str(sequence)}, "method": "aop.subscribe", "params":[]}}'
        # ^^ let op ik moest text accolades dubbel gebruiken anders werden ze geinterpreteerd als fstring accolades
        await websocket.send(sub_msg)
        async for message in websocket:
            try:
                message = json.loads(message)
                if "error" in message:
                    await asyncio.sleep(0.01)
                if message["type"] == "incremental":
                    order_status = message["orders"]
                    order_status = order_status[-1]
                    message = {"Type": "Orderstatus", "Exchange": "PHEMEX", "Orderstatus": order_status}
                    message = json.dumps(message)
                    await socket.send_string(message)
            except Exception as e:
                print(f"EXCEPT 1b: {e}")


async def websocket_ftx():
    socket = ctx.socket(zmq.PUSH)
    socket.connect("tcp://172.31.41.224:5557")
    print("Ftx ZMQ socket connected.")
    ftx_bid = ""
    ftx_ask = ""
    order_status = {}
    async with websockets.connect("wss://ftx.com/ws/") as websocket:
        await websocket.send(msg_ftx)
        msg1 = {'op': 'subscribe', 'channel': 'ticker', 'market': spot_market}
        msg1 = json.dumps(msg1)
        await websocket.send(msg1)
        msg2 = {'op': 'subscribe', 'channel': 'orders'}
        msg2 = json.dumps(msg2)
        await websocket.send(msg2)
        async for message in websocket:
            try:
                message = json.loads(message)
            except Exception as e:
                print(f"EXCEPT 1c: {e}")
            else:
                if message['channel'] == 'ticker' and message['type'] == 'update':
                    if (str(message["data"]["bid"]) != ftx_bid) or (str(message["data"]["ask"]) != ftx_ask):
                        ftx_bid = str(message["data"]["bid"])
                        ftx_ask = str(message["data"]["ask"])
                        message = {"Type": "Ticker", "Exchange": "FTX", "Bid": ftx_bid, "Ask": ftx_ask}
                        message = json.dumps(message)
                        await socket.send_string(message)

                elif message['channel'] == 'orders' and message['type'] == 'update':
                    if message["data"] != order_status:
                        order_status = message["data"]
                        message = {"Type": "Orderstatus", "Exchange": "FTX", "Orderstatus": order_status}
                        message = json.dumps(message)
                        await socket.send_string(message)


async def data_handler():
    # first get static api data via aiohttp
    phemex_data = await phemex_api()
    price_incr_perp = phemex_data[0]
    price_scale = 10000  # let op dit kan in de toekomst misschien varieren

    spot_data = await ftx_api()
    price_incr_spot = spot_data[0]

    # # ALWAYS USE PRICE INCREMENT OF EXCHANGE WITH LARGEST INCREMENT VALUES !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    # if price_incr_spot > price_incr_perp:
    #     price_incr_perp = price_incr_spot

    num_decimals = str(price_incr_perp)[::-1].find('.')

    spread_array = np.array([])
    exit_spread_array = np.array([])
    maximum_items = 100  # 10000
    minimum_items = 50
    stdev_num = 1

    socket = ctx.socket(zmq.PULL)
    socket.bind("tcp://172.31.41.224:5557")

    socket2 = ctx.socket(zmq.PUSH)
    socket2.connect("tcp://172.31.41.224:5559")

    print("Data handler initiated.")

    perp_ask = 0
    spot_ask = 0
    perp_bid = 0
    spot_bid = 0
    ftx_orderstatus = {}
    phemex_orderstatus = {}

    while True:
        received_message = await socket.recv_json()
        await asyncio.sleep(0.00001)

        if (received_message['Type'] == 'Ticker') and (received_message['Exchange'] == 'PHEMEX'):
            perp_bid = float(received_message['Bid']) / price_scale  # !!!!!!!!!!!!!!!!!!!!!!!!
        if (received_message['Type'] == 'Ticker') and (received_message['Exchange'] == 'PHEMEX'):
            perp_ask = float(received_message['Ask']) / price_scale  # !!!!!!!!!!!!!!!!!!!!!!!!
        if (received_message['Type'] == 'Ticker') and (received_message['Exchange'] == 'FTX'):
            spot_bid = float(received_message['Bid'])
        if (received_message['Type'] == 'Ticker') and (received_message['Exchange'] == 'FTX'):
            spot_ask = float(received_message['Ask'])
        if (received_message['Type'] == 'Orderstatus') and (received_message['Exchange'] == 'FTX'):
            ftx_orderstatus = dict(received_message['Orderstatus'])
        if (received_message['Type'] == 'Orderstatus') and (received_message['Exchange'] == 'PHEMEX'):
            phemex_orderstatus = dict(received_message['Orderstatus'])
            await asyncio.sleep(0.00001)




        if (perp_ask != 0) and (spot_ask != 0) and (perp_bid != 0) and (spot_bid != 0):

            # 0.05% LET OP DIT IS EEN EXPERIMENT ivm FTX LIMIT ORDERS IPV MARKET!!!!!!!!!!!
            perp_entry_ask = (spot_bid * 1.0005) # 1.0005 WAS SUCCESVOL
            if perp_entry_ask > perp_ask:
                # FINETUNE PERP ASK PRICE (IVM MIN PRICE INCREMENTS PHEMEX) !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                perp_entry_ask = perp_entry_ask - (perp_entry_ask % price_incr_perp)
                perp_entry_ask = round(perp_entry_ask, num_decimals)
                # ^^ round is extra omdat er soms toch meer decimalen overblijven
                perp_entry_ask = perp_entry_ask + price_incr_perp
                # ^^ eerst rond ik het af naar beneden, daarna doe ik + 1 increment == naar boven afronden
                perp_entry_ask = round(perp_entry_ask, num_decimals)
            else:
                perp_entry_ask = perp_ask

            # 0.03% LET OP DIT IS EEN EXPERIMENT ivm FTX LIMIT ORDERS IPV MARKET!
            perp_exit_bid = spot_ask * 0.9995  # 0.9995 WAS SUCCESVOL
            if perp_exit_bid < perp_bid:
                # FINETUNE PERP BID PRICE (IVM MIN PRICE INCREMENTS PERPS PHEMEX) !!!!!!!!!!!!!!!!!!!!!!!!!!!!
                perp_exit_bid = perp_exit_bid - (perp_exit_bid % price_incr_perp)
                perp_exit_bid = round(perp_exit_bid, num_decimals)
                # ^^ round is extra omdat er soms toch meer decimalen overblijven
                perp_exit_bid = perp_exit_bid - price_incr_perp
                # ^^ eerst rond ik het af naar boven, daarna doe ik - 1 increment == naar beneden afronden
                perp_exit_bid = round(perp_exit_bid, num_decimals)

            else:
                perp_exit_bid = perp_bid





            message = {"Perp entry ask": perp_entry_ask, "Perp exit bid": perp_exit_bid, "ftx_spot_bid": spot_bid,
                       "ftx_spot_ask": spot_ask, "phemex_perp_bid": perp_bid, "phemex_perp_ask": perp_ask,
                       "ftx_orderstatus": ftx_orderstatus, "phemex_orderstatus": phemex_orderstatus}
            message = json.dumps(message)
            await socket2.send_string(message)
        else:
            print("Data-handler: Waiting for websocket data...")
        await asyncio.sleep(0.00001)


async def order_execution():
    global RATE_LIMIT_HIT
    # PHEMEX PERP STATIC DATA : AIOHTTP ASYNC API REQUEST!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    phemex_data = await phemex_api()
    price_incr_perp = phemex_data[0]
    size_incr_perp = phemex_data[1]
    min_size_perp = phemex_data[1]
    price_scale = phemex_data[2]
    # "priceScale" is uniek voor PHEMEX en heb ik later nog nodig om de orderprijs om te rekenen naar "ep" price
    # bij account currencies (USD en BTC) heet dit "valueScale"

    # FTX spot STATIC DATA : AIOHTTP ASYNC API REQUEST  --> FTX SPOT determines position size!!!!!!!!!!!!!!!!!!
    spot_data = await ftx_api()
    price_incr_spot = spot_data[0]
    size_incr_spot = spot_data[1]
    min_size_spot = spot_data[2]

    # ALWAYS USE SIZE INCREMENT OF EXCHANGE WITH LARGEST INCREMENT VALUES
    if size_incr_spot > size_incr_perp:
        size_incr_perp = size_incr_spot

    print("Connecting to ZMQ server...")
    socket2 = ctx.socket(zmq.PULL)
    socket2.bind("tcp://172.31.41.224:5559")

    # IMPORTANT: WAIT FOR ZQN DATA TO COME IN
    while (await socket2.recv_json()) == "":
        await asyncio.sleep(0.00001)

    while not RATE_LIMIT_HIT:
        print("NEW ORDER EXECUTION INITIATION.")

        # FTX BALANCE
        ftx_available_USD = await ftx_api_balance()
        print(f"FTX AVAILABLE USD BALANCE: {ftx_available_USD}")

        # PHEMEX BALANCE
        phemex_available_USD = await phemex_api_balance("USD")
        print(f"PHEMEX AVAILABLE USD BALANCE: {phemex_available_USD}")

        # CHECK IF FTX BALANCE IS BIG ENOUGH (TO HEDGE BYBIT POSITION)
        if phemex_available_USD > ftx_available_USD:
            available_USD = ftx_available_USD
        else:
            available_USD = phemex_available_USD

        ask_price_new = (await socket2.recv_json())['Perp entry ask']
        print("ASK PRICE: ", ask_price_new)

        # DETERMINE ORDER SIZE
        price_per_contract = ask_price_new * size_incr_perp  # vraagprijs * (aantal coins in 1 contract)
        order_size = int(available_USD / price_per_contract)  # aantal contracts
        print(f"ORDER SIZE: {order_size}")

        if (order_size < min_size_spot) or (order_size < min_size_perp):
            print(f"{spot_market}: INITIAL ORDER FAILED: SIZE TOO LOW FOR MIN REQUIRED SIZE")
        await asyncio.sleep(0.0001)

        # PLACE INITIAL PHEMEX PERP LIMIT ORDER ------------------------------------------------------------------------
        clOrdID = str(int(time.time() * 1000))
        # remove decimals door priceScale van 4 (*10.000) heeft geen perp decimalen
        # moeten verwijderd (met int) (anders slaat phemex op hol)
        ask_price_new = int((await socket2.recv_json())['Perp entry ask'] * price_scale)
        print("ASK PRICE: ", ask_price_new)

        order = await phemex_place_order(symbol=future_market, clOrdID=clOrdID, side="Sell", orderQty=order_size,
                                         priceEp=ask_price_new, ordType="Limit", timeInForce="PostOnly",
                                         reduceOnly="false")
        order_id = order["data"]["orderID"]

        # WAIT FOR ORDERSTATUS
        print("WAITING FOR INITIAL ENTRY ORDER STATUS")
        phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
        while phemex_orderstatus == {}:
            phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
            await asyncio.sleep(0.001)

        # WAIT FOR "NEW" ORDERSTATUS
        phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
        while phemex_orderstatus["clOrdID"] != clOrdID:
            print("WAITING FOR 'NEW' ORDER STATUS CONFIRMATION")
            phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
            await asyncio.sleep(0.001)
        print(f"INITIAL PERP ORDER SUCCESS: {phemex_orderstatus}")

        # ----- WHILE LOOP -----
        # UPDATE LOOP UNTIL PERP ORDER FILLED -> na partial fill al gaan hedgen (dus exit loop zodra 'cumQty' != 0)
        data = (await socket2.recv_json())
        perp_entry_ask = data['Perp entry ask']
        cumQty = data["phemex_orderstatus"]["cumQty"]
        order_price = (data["phemex_orderstatus"]["priceEp"]) / price_scale
        exec_status = data["phemex_orderstatus"]["execStatus"]

        while cumQty == 0:  # let op bij partial fill al hedgen (anders kan je door meerdere fills niet goed hedgen)
            print("NEW ENTRY WHILE LOOP IERATION")
            # print("TOTAL NR. OF API REQUESTS: ", NUM_REQUESTS)
            # print(f"order price {order_price} + increment {price_incr_perp} = {order_price + price_incr_perp}")
            # print("perp entry ask: ", perp_entry_ask)
            if (perp_entry_ask > (order_price + price_incr_perp) or
                perp_entry_ask < (order_price - price_incr_perp)) and \
                    (exec_status not in ("Expired", "Canceled", "Rejected")) and (cumQty == 0):
                print(f"TIME PERP ENTRY 0 = {datetime.now()}")
                try:
                    new_price = int((await socket2.recv_json())['Perp entry ask'] * price_scale)
                    print("NEW PRICE: ", new_price)
                    clOrdID = str(int(time.time() * 1000))
                    order = await phemex_replace_order(symbol=future_market, orderID=order_id, priceEp=new_price,
                                                       clOrdID=clOrdID)
                    order_id = order["data"]["orderID"]
                except Exception as e:
                    print(f"EXCEPT 1d: {e}")
                else:
                    phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
                    while phemex_orderstatus["clOrdID"] != clOrdID:
                        await asyncio.sleep(0.00001)
                        print("orderstatus wait 1")
                        phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
                    print("perp order REPLACED")

                print(f"TIME PERP ENTRY 1 = {datetime.now()}")
                await asyncio.sleep(0.00001)

            phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
            cumQty = phemex_orderstatus["cumQty"]
            execQty = phemex_orderstatus["execQty"]
            exec_status = phemex_orderstatus["execStatus"]
            # cumQty GEEFT ALLEEN AANTAL WEER NA FILL : deze moet je hebben voor while loop: zolang cumQty == 0 geen fill
            # execQty GEEFT AANTAL WEER NA FILL EN OOK NA CANCEL/EXPIRY : deze moet je hebben als remaining size na cancel/expiry

            if exec_status in ("Expired", "Canceled", "Rejected") and (cumQty == 0):
                try:
                    new_price = int((await socket2.recv_json())['Perp entry ask'] * price_scale)
                    clOrdID = str(int(time.time() * 1000))
                    order = await phemex_place_order(symbol=future_market, clOrdID=clOrdID, side="Sell",
                                                     orderQty=execQty, priceEp=new_price, ordType="Limit",
                                                     timeInForce="PostOnly", reduceOnly="false")
                    order_id = order["data"]["orderID"]
                except Exception as e:
                    print(f"EXCEPT 2: {e}")
                else:
                    # phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
                    while phemex_orderstatus["clOrdID"] != clOrdID:
                        await asyncio.sleep(0.00001)
                        print("orderstatus wait 2!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                        phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
                    print("NEW perp order placed")
                print(f"TIME PERP ENTRY 2 = {datetime.now()}")

            data = (await socket2.recv_json())
            perp_entry_ask = data['Perp entry ask']
            phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
            cumQty = phemex_orderstatus["cumQty"]
            order_price = phemex_orderstatus["priceEp"] / price_scale
            exec_status = phemex_orderstatus["execStatus"]

        await phemex_cancel_order(symbol=future_market, orderID=order_id)   # cancel potential remaining order !!!!!!!
        order_status = (await socket2.recv_json())["phemex_orderstatus"]
        print(order_status)
        order_size = order_status["execQty"]  # determine PERP SIZE because of partial fills!!!!!!!!!!!!!!!!!!!!!
        print("PHEMEX TOTAL PERP FILLED SIZE (CHECKEN OF KLOPT): ", order_size)
        print("PHEMEX PERP LIMIT ORDER FILLED. SENDING FTX SPOT LIMIT BUY ORDER.")

        # -------------------------------------------------------------------------------------------------------------
        # SPOT MAKER HEDGE CODE !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        spot_size = order_size
        spot_bid = (await socket2.recv_json())['ftx_spot_bid']
        # place order (OPEN BUY SPOT)
        try:
            client_ftx.place_order(market=f"{spot_market}", side="buy", price=spot_bid + price_incr_spot,
                                                     type="limit", size=spot_size, post_only=True,
                                                     reduce_only=False)
        except Exception as e:
            try:
                client_ftx.place_order(market=f"{spot_market}", side="buy", price=spot_bid,
                                                            type="limit", size=spot_size, post_only=True,
                                                            reduce_only=False)
            except Exception as e:
                print("ERROR: ", e)
                await asyncio.sleep(1000) #EXTREME SLEEP SO I CAN NOTICE THE ISSUE

        order_status = (await socket2.recv_json())['ftx_orderstatus']
        while order_status == {}:
            order_status = (await socket2.recv_json())['ftx_orderstatus']
            await asyncio.sleep(0.0001)

        print("starting entry while loop spot")
        while order_status["filledSize"] != order_status["size"]:     # filled_size !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            order_status = (await socket2.recv_json())['ftx_orderstatus']
            print("SPOT ENTRY 0")
            # ORDER NIET MEER OP BEST BID? CANCELEN!!!
            if (order_status["price"] != (await socket2.recv_json())['ftx_spot_bid']) \
                    and order_status["status"] != "closed" and (order_status["filledSize"] != order_status["size"]):  # filled_size !!!!!!!!!!!!!!!!!!!!!!!!!!!
                # .... then cancel order
                try:
                    client_ftx.cancel_order(order_id=order_status["id"])
                except Exception as e:
                    print(f"Spot order cancel failed. {e}")
                else:
                    order_status = (await socket2.recv_json())['ftx_orderstatus']
                    while order_status["status"] != "closed":
                        order_status = (await socket2.recv_json())['ftx_orderstatus']
                        await asyncio.sleep(0.0001)
                print("SPOT ENTRY 1")
                # GECANCELDE ORDER REPLACEN DOOR NEW ORDER OP:
                # SPOT BID PLUS INCREMENT  !!!!!!
                order_status = (await socket2.recv_json())['ftx_orderstatus']
                if order_status["status"] == "closed" and (order_status["filledSize"] != order_status["size"]): # filled_size !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                    remaining_size = order_status["size"] - order_status["filledSize"]  # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                    try:
                        old_order_id = order_status["id"]
                        new_bid = (await socket2.recv_json())['ftx_spot_bid'] + price_incr_spot
                        client_ftx.place_order(market=f"{spot_market}", side="buy",
                                                                price=new_bid, type="limit",
                                                                size=remaining_size, post_only=True,
                                                                reduce_only=False)
                    except Exception as e:
                        print(f"New spot order failed 1. {e}")
                    else:
                        order_status = (await socket2.recv_json())['ftx_orderstatus']
                        while order_status["id"] == old_order_id:
                            order_status = (await socket2.recv_json())['ftx_orderstatus']
                            await asyncio.sleep(0.0001)
                    print("SPOT ENTRY 2")
            print("SPOT ENTRY 3")
            # ALS ORDER NIET DOOR ONS GECANCELD IS: NEW ORDER OP:
            # SPOT BID ZONDER INCREMENT
            if order_status["status"] == "closed" and (order_status["filledSize"] != order_status["size"]): # filled_size !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                remaining_size = order_status["size"] - order_status["filledSize"]  # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                try:
                    old_order_id = order_status["id"]
                    new_bid = (await socket2.recv_json())['ftx_spot_bid']  # ZONDER EXTRA INCREMENT!!!!!!!!!!!!
                    client_ftx.place_order(market=f"{spot_market}", side="buy",
                                                       price=new_bid, type="limit",
                                                       size=remaining_size, post_only=True,
                                                       reduce_only=False)
                except Exception as e:
                    print(f"New spot order failed 2. {e}")
                else:
                    order_status = (await socket2.recv_json())['ftx_orderstatus']
                    while order_status["id"] == old_order_id:
                        order_status = (await socket2.recv_json())['ftx_orderstatus']
                        print("Waiting for new order.")
                        await asyncio.sleep(0.0001)
                print("SPOT ENTRY 4")
            await asyncio.sleep(0.001)
            print("SPOT ENTRY 5")

        print(order_status)
        print("SPOT order filled. Hurray.")
        print("You are fully HEDGED now")

        # --------------------------------------------------------------------------------------------------------------
        # EXIT CODE!!!!!!
        # --------------------------------------------------------------------------------------------------------------

        print("Entered exit code.")

        clOrdID = str(int(time.time() * 1000))
        # remove decimals door priceScale van 4 (*10.000) heeft geen perp decimalen
        # moeten verwijderd (met int) (anders slaat phemex op hol)
        bid_price_new = (await socket2.recv_json())['Perp exit bid']
        bid_price_new = int(bid_price_new * price_scale)
        print("BID PRICE: ", bid_price_new)

        # PLACE INITIAL PHEMEX PERP LIMIT EXIT ORDER ------------------------------------------------------------------------
        order = await phemex_place_order(symbol=future_market, clOrdID=clOrdID, side="Buy", orderQty=order_size,
                                         priceEp=bid_price_new, ordType="Limit", timeInForce="PostOnly",
                                         reduceOnly="true")
        # let op!! reduce only is true (exit)
        order_id = order["data"]["orderID"]

        # WAIT FOR INITIAL EXIT ORDERSTATUS
        print("WAITING FOR INITIAL EXIT ORDER STATUS")
        phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
        while phemex_orderstatus["clOrdID"] != clOrdID:  # checken of we niet naar de status vd vorige order kijken
            phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
            await asyncio.sleep(0.001)

        # # WAIT FOR "NEW" ORDERSTATUS
        # phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
        # while phemex_orderstatus["clOrdID"] == clOrdID and (phemex_orderstatus["ordStatus"] not in ("New", "Filled")):
        #     print("WAITING FOR 'NEW' EXIT ORDER STATUS CONFIRMATION")
        #     phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
        #     await asyncio.sleep(0.001)
        # print(f"INITIAL PERP EXIT ORDER SUCCESS: {phemex_orderstatus}")

        # ----- WHILE LOOP -----
        # UPDATE LOOP UNTIL PERP EXIT ORDER 100% FILLED -> GEEN PARTIAL FILL (ZOALS BY ENTRY) MAAR WACHTEN TOT ALLES OP IS.
        data = (await socket2.recv_json())
        perp_exit_bid = data['Perp exit bid']
        cumQty = data["phemex_orderstatus"]["cumQty"]
        order_price = (data["phemex_orderstatus"][
            "priceEp"]) / price_scale  # BY PHEMEX MOET JE "EP" PRIJS OMREKENEN MBV PRICESCALE OF VALUESCALE WAARDE
        exec_status = data["phemex_orderstatus"]["execStatus"]
        order_id = data["phemex_orderstatus"]["orderID"]

        while cumQty != order_size:  # LET OP: WACHT OP 100% FILL (GEEN PARTIAL FILL ZOALS BIJ ENTRY)
            print("NEW PERP EXIT WHILE LOOP IERATION")
            # print("TOTAL NR. OF API REQUESTS: ", NUM_REQUESTS)
            # print(f"order price {order_price} + increment {price_incr_perp} = {order_price + price_incr_perp}")
            # print("perp exit bid: ", perp_exit_bid)
            if (perp_exit_bid > (order_price + price_incr_perp) or \
                perp_exit_bid < (order_price - price_incr_perp)) and \
                    (exec_status not in ("Expired", "Canceled", "Rejected")) and (cumQty != order_size):
                print(f"TIME PERP EXIT 0 = {datetime.now()}")

                try:
                    new_price = int(perp_exit_bid * price_scale)
                    clOrdID = str(int(time.time() * 1000))
                    order = await phemex_replace_order(symbol=future_market, orderID=order_id, priceEp=new_price,
                                                       clOrdID=clOrdID)
                    order_id = order["data"]["orderID"]
                except Exception as e:
                    print(f"EXCEPT 1e: {e}")
                    print(order)
                else:
                    phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
                    while phemex_orderstatus["clOrdID"] != clOrdID:
                        await asyncio.sleep(0.00001)
                        print("orderstatus wait 1")
                        phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
                    print("perp EXIT order REPLACED")

                print(f"TIME PERP ENTRY 1 = {datetime.now()}")
                await asyncio.sleep(0.00001)

            phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
            exec_status = phemex_orderstatus["execStatus"]
            cumQty = phemex_orderstatus["cumQty"]
            execQty = phemex_orderstatus["execQty"]
            # cumQty GEEFT ALLEEN AANTAL WEER NA FILL :
            # - stoppen na partial fill: zolang (cumQty == 0) , geen partial fill
            # - stoppen na 100% fill: zolang (cumQty != order_size), geen 100% fill
            # execQty GEEFT AANTAL WEER NA FILL EN OOK NA CANCEL/EXPIRY : deze moet je hebben als remaining size na cancel/expiry

            if exec_status in ("Expired", "Canceled", "Rejected") and (cumQty != order_size):
                try:
                    new_clOrdID = str(int(time.time() * 1000))
                    await phemex_place_order(symbol=future_market, clOrdID=new_clOrdID, side="Buy", orderQty=execQty,
                                             priceEp=int((await socket2.recv_json())['Perp exit bid'] * price_scale),
                                             ordType="Limit", timeInForce="PostOnly", reduceOnly="true")
                    # let op!! reduce only is true (exit)
                    order_id = order["data"]["orderID"]
                except Exception as e:
                    print(f"EXCEPT 2: {e}")
                else:
                    phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
                    while phemex_orderstatus["clOrdID"] != new_clOrdID:
                        await asyncio.sleep(0.00001)
                        print("orderstatus wait 2!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                        phemex_orderstatus = (await socket2.recv_json())['phemex_orderstatus']
                    print("new perp exit order placed")
                print(f"TIME PERP EXIT 2 = {datetime.now()}")

            data = (await socket2.recv_json())
            perp_exit_bid = data['Perp exit bid']
            cumQty = data["phemex_orderstatus"]["cumQty"]
            order_price = (data["phemex_orderstatus"]["priceEp"]) / price_scale
            exec_status = data["phemex_orderstatus"]["execStatus"]
            order_id = data["phemex_orderstatus"]["orderID"]

        order_status = (await socket2.recv_json())["phemex_orderstatus"]
        print(order_status)
        order_size = order_status["execQty"]  # re-determine PERP SIZE because of partial fills!!!!!!!!!!!!!!!!!!!!!
        print("PHEMEX TOTAL PERP FILLED SIZE (CHECKEN OF KLOPT): ", order_size)
        print("PHEMEX PERP LIMIT BUY EXIT ORDER FILLED. SENDING FTX SPOT MARKET EXIT SELL ORDER.")

        # -------------------------------------------------------------------------------------------------------------
        # PLACE FTX SPOT EXIT LIMIT ORDER ----------------------------------------------------------------------------
        # -------------------------------------------------------------------------------------------------------------
        # SPOT MAKER HEDGE CODE !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        spot_size = order_size
        print("SPOT EXIT ORDER SIZE: ", spot_size)
        spot_ask = (await socket2.recv_json())['ftx_spot_ask']
        print("SPOT EXIT ASK PRICE: ", spot_ask)

        # place order (EXIT SELL SPOT)
        try:
            old_order_id = (await socket2.recv_json())['ftx_orderstatus']["id"]
            client_ftx.place_order(market=f"{spot_market}", side="sell", price=spot_ask - price_incr_spot,
                                                     type="limit", size=spot_size, post_only=True,
                                                     reduce_only=True)
        except Exception as e:
            print("Intial spot EXIT order failed. Retry.", e)
            try:
                old_order_id = order_status["id"]
                client_ftx.place_order(market=f"{spot_market}", side="sell", price=spot_ask,
                                                            type="limit", size=spot_size, post_only=True,
                                                            reduce_only=True)
            except Exception as e:
                print("Intial spot EXIT order failed again.", e)
                await asyncio.sleep(1000) #EXTREME SLEEP SO I CAN NOTICE THE ISSUE

        order_status = (await socket2.recv_json())['ftx_orderstatus']
        while order_status["id"] == old_order_id:
            order_status = (await socket2.recv_json())['ftx_orderstatus']
            print("Waiting for initial spot EXIT spot order status.")
            await asyncio.sleep(0.0001)

        print("starting exit while loop spot")
        while order_status["filledSize"] != order_status["size"]:  # filled_size !!!!!!!!!!!!!!!!!!!!!!!!
            order_status = (await socket2.recv_json())['ftx_orderstatus']

            # ORDER NIET MEER OP BEST ASK? CANCELEN!!!
            if (order_status["price"] != (await socket2.recv_json())['ftx_spot_ask']) \
                    and order_status["status"] != "closed" and (order_status["filledSize"] != order_status["size"]):  # filled_size !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                # .... then cancel order
                try:
                    client_ftx.cancel_order(order_id=order_status["id"])
                except Exception as e:
                    print("Spot EXIT order cancel failed.", e)
                else:
                    order_status = (await socket2.recv_json())['ftx_orderstatus']
                    while order_status["status"] != "closed":
                        order_status = (await socket2.recv_json())['ftx_orderstatus']
                        print("Waiting for EXIT spot order cancelation.")
                        await asyncio.sleep(0.0001)

                # GECANCELDE ORDER REPLACEN DOOR NEW ORDER OP:
                # SPOT ASK MINUS INCREMENT  !!!!!!
                order_status = (await socket2.recv_json())['ftx_orderstatus']
                if order_status["status"] == "closed" and (order_status["filledSize"] != order_status["size"]): # filled_size !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                    remaining_size = order_status["size"] - order_status["filledSize"]   #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                    try:
                        old_order_id = order_status["id"]
                        new_ask = (await socket2.recv_json())['ftx_spot_ask'] - price_incr_spot
                        client_ftx.place_order(market=f"{spot_market}", side="sell",
                                                                price=new_ask, type="limit",
                                                                size=remaining_size, post_only=True,
                                                                reduce_only=True)
                    except Exception as e:
                        print("New spot EXIT spot order failed 1.", e)
                    else:
                        order_status = (await socket2.recv_json())['ftx_orderstatus']
                        while order_status["id"] == old_order_id:
                            order_status = (await socket2.recv_json())['ftx_orderstatus']
                            print("Waiting for new EXIT spot order 1.")
                            await asyncio.sleep(0.0001)


            # SPOT ASK ZONDER INCREMENT
            if order_status["status"] == "closed" and (order_status["filledSize"] != order_status["size"]): # filled_size !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                remaining_size = order_status["size"] - order_status["filledSize"]  # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                try:
                    old_order_id = order_status["id"]
                    new_ask = (await socket2.recv_json())['ftx_spot_ask']  # ZONDER EXTRA INCREMENT!!!!!!!!!!!!
                    client_ftx.place_order(market=f"{spot_market}", side="sell",
                                                            price=new_ask, type="limit",
                                                            size=remaining_size, post_only=True,
                                                            reduce_only=True)
                except Exception as e:
                    print("New spot EXIT spot order failed 2.", e)
                    print(order_status)
                else:
                    order_status = (await socket2.recv_json())['ftx_orderstatus']
                    while order_status["id"] == old_order_id:
                        order_status = (await socket2.recv_json())['ftx_orderstatus']
                        print("Waiting for new EXIT spot order 2.")
                        await asyncio.sleep(0.0001)

            await asyncio.sleep(0.001)

        print("SPOT hedge closed. Hurray.")
        print("Both positions are closed now")
        print("initiating a new trade in 10 sec")
        await asyncio.sleep(10)


# BELOW THE AIOHTTP SIZE/PRICE INCREMENT COROUTINES:
async def phemex_api():
    global NUM_REQUESTS
    async with aiohttp.ClientSession() as phemex_session1:
        async with phemex_session1.get(normal_api + '/public/products',
                                       ssl_context=ssl_context) as resp_phemex1:  # belangrijk! voorkomt ssl error op AWS!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            data = await resp_phemex1.text()
            symbol_info = json.loads(data)["data"]["products"]
            symbol_info = [item for item in symbol_info if item['symbol'] == future_market][0]
            size_increment = float(symbol_info['contractSize'])
            price_increment = float(symbol_info['tickSize'])
            price_scale = float(symbol_info['priceScale'])
            price_scale = 10 ** price_scale
    await phemex_session1.close()
    NUM_REQUESTS += 1
    print("TOTAL NR. OF REQUESTS: ", NUM_REQUESTS)
    return [price_increment, size_increment, price_scale]


async def ftx_api():
    async with aiohttp.ClientSession() as ftx_session1:
        async with ftx_session1.get(f'https://ftx.com/api/markets/{spot_market}') as resp_ftx1:
            ftx_data = await resp_ftx1.text()
            ftx_data = json.loads(ftx_data)["result"]
            price_incr_spot = ftx_data["priceIncrement"]
            size_incr_spot = ftx_data["sizeIncrement"]
            min_size_spot = ftx_data["minProvideSize"]
    await ftx_session1.close()
    return [price_incr_spot, size_incr_spot, min_size_spot]


# BELOW THE AIOHTTP BALANCE COROUTINES
async def phemex_api_balance(symbol):
    global NUM_REQUESTS
    ssl_context = ssl.create_default_context(
        cafile=certifi.where())  # 3/4 belangrijk! voorkomt ssl error op AWS!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    balance_phemex_coin = symbol  # CONTRACTS (PERP) ACCOUNT BALANCE/POSITIONS DATA IS IN BTC OR USD
    expiry = str(trunc(time.time()) + 60)
    endpoint = "/accounts/accountPositions"
    query_string = f"currency={balance_phemex_coin}"  # CONTRACTS (PERP) ACCOUNT BALANCE/POSITIONS DATA IS IN BTC OR USD
    message = endpoint + query_string + expiry
    signature = hmac.new(api_secret_phemex.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
    header = {
        'x-phemex-request-signature': signature.hexdigest(),
        'x-phemex-request-expiry': expiry,
        'x-phemex-access-token': api_key_phemex,
        'Content-Type': 'application/json'}
    api_url = normal_api + endpoint + '?' + query_string
    async with aiohttp.ClientSession(headers=header) as phemex_session2:
        async with phemex_session2.get(url=api_url,
                                       ssl_context=ssl_context) as resp_phemex2b:  # belangrijk! voorkomt ssl error op AWS!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            try:
                phemex_available_USD = await resp_phemex2b.text()
            except Exception as e:
                print(f"PHEMEX API BALANCE REQUEST ERROR: {e}")
            else:
                phemex_available_USD = json.loads(phemex_available_USD)
                phemex_available_USD = phemex_available_USD["data"]["account"]["accountBalanceEv"]
                phemex_available_USD = phemex_available_USD / 10000  # belangrijk, deze zou kunnen veranderen, af en toe checken
                phemex_available_USD = (phemex_available_USD * 0.90)  # LET OP!! NEEM 90% VAN USD BALANCE
    await phemex_session2.close()
    NUM_REQUESTS += 1
    print("TOTAL NR. OF REQUESTS: ", NUM_REQUESTS)
    return phemex_available_USD


async def ftx_api_balance():
    balance_ftx_coin = "USD"
    API = os.environ.get("API_FTX")
    API_secret = os.environ.get("SECRET_FTX")
    url = "https://ftx.com/api/wallet/balances"

    ts = str(int(time.time() * 1000))
    signature_payload = f'{ts}GET/api/wallet/balances'.encode()
    signature = hmac.new(API_secret.encode(), signature_payload, 'sha256').hexdigest()
    headers = {
        "FTX-KEY": API,
        "FTX-SIGN": signature,
        "FTX-TS": (ts),
        'FTX-SUBACCOUNT': "1"
    }
    async with aiohttp.ClientSession() as ftx_session:
        async with ftx_session.get(url, headers=headers) as resp_ftx:
            try:
                ftx_available_USD = await resp_ftx.text()
            except Exception as e:
                print(f"FTX API BALANCE REQUEST ERROR: {e}")
            else:
                ftx_available_USD = json.loads(ftx_available_USD)
                ftx_available_USD = ftx_available_USD["result"]
                ftx_available_USD = [item["free"] for item in ftx_available_USD if item["coin"] == balance_ftx_coin][0]
                ftx_available_USD = (ftx_available_USD * 0.90)
                # 90% of account: omdat we niet exakt weten voor welke prijs de order wordt uitgevoerd.
    await ftx_session.close()
    return ftx_available_USD


async def phemex_place_order(symbol, clOrdID, side, orderQty, priceEp, ordType, timeInForce, reduceOnly):
    global NUM_REQUESTS
    expiry = str(trunc(time.time()) + 60)
    endpoint = "/orders/create"
    query_string = f"symbol={symbol}&clOrdID={clOrdID}&side={side}&orderQty={orderQty}&priceEp={priceEp}&ordType={ordType}&timeInForce={timeInForce}&reduceOnly={reduceOnly}"  # CONTRACTS (PERP) ACCOUNT BALANCE/POSITIONS DATA IS IN BTC OR USD
    message = endpoint + query_string + expiry
    signature = hmac.new(api_secret_phemex.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
    header = {
        'x-phemex-request-signature': signature.hexdigest(),
        'x-phemex-request-expiry': expiry,
        'x-phemex-access-token': api_key_phemex,
        'Content-Type': 'application/json'}
    api_url = normal_api + endpoint + '?' + query_string
    async with aiohttp.ClientSession(headers=header) as phemex_session:
        async with phemex_session.put(api_url,
                                      ssl_context=ssl_context) as resp_phemex:  # belangrijk! voorkomt ssl error op AWS!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            try:
                order = await resp_phemex.text()
                order = json.loads(order)
                # order = order["data"]
            except Exception as e:
                print(f"PHEMEX ORDER ERROR: {e}")
    await phemex_session.close()
    NUM_REQUESTS += 1
    print("TOTAL NR. OF REQUESTS: ", NUM_REQUESTS)
    return order


async def phemex_replace_order(symbol, orderID, clOrdID, priceEp):
    global NUM_REQUESTS
    expiry = str(trunc(time.time()) + 60)
    endpoint = "/orders/replace"
    query_string = f"symbol={symbol}&orderID={orderID}&clOrdID={clOrdID}&priceEp={priceEp}"
    message = endpoint + query_string + expiry
    signature = hmac.new(api_secret_phemex.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
    header = {
        'x-phemex-request-signature': signature.hexdigest(),
        'x-phemex-request-expiry': expiry,
        'x-phemex-access-token': api_key_phemex,
        'Content-Type': 'application/json'}
    api_url = normal_api + endpoint + '?' + query_string
    async with aiohttp.ClientSession(headers=header) as phemex_session4:
        async with phemex_session4.put(api_url,
                                       ssl_context=ssl_context) as resp_phemex4:  # belangrijk! voorkomt ssl error op AWS!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            try:
                order = await resp_phemex4.text()
                order = json.loads(order)
                # order = order["data"]
            except Exception as e:
                print(f"PHEMEX REPLACE ORDER ERROR: {e}")
    await phemex_session4.close()
    NUM_REQUESTS += 1
    print("TOTAL NR. OF REQUESTS: ", NUM_REQUESTS)
    return order


async def phemex_cancel_order(symbol, orderID):
    global NUM_REQUESTS
    expiry = str(trunc(time.time()) + 60)
    endpoint = "/orders/cancel"
    query_string = f"symbol={symbol}&orderID={orderID}"  # CONTRACTS (PERP) ACCOUNT BALANCE/POSITIONS DATA IS IN BTC OR USD
    message = endpoint + query_string + expiry
    signature = hmac.new(api_secret_phemex.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
    header = {
        'x-phemex-request-signature': signature.hexdigest(),
        'x-phemex-request-expiry': expiry,
        'x-phemex-access-token': api_key_phemex,
        'Content-Type': 'application/json'}
    api_url = normal_api + endpoint + '?' + query_string
    async with aiohttp.ClientSession(headers=header) as phemex_session:
        async with phemex_session.put(api_url, ssl_context=ssl_context) as resp_phemex:  # belangrijk!
            try:
                order = await resp_phemex.text()
                order = json.loads(order)
                order = order["data"]
            except Exception as e:
                print(f"PHEMEX CANCEL ORDER ERROR: {e}")
    await phemex_session.close()
    NUM_REQUESTS += 1
    print("TOTAL NR. OF REQUESTS: ", NUM_REQUESTS)
    return order



# GLOBAL VARIABLE THAT COUNTS ALL REQUESTS (IVM RATE LIMITS)
# Contracts: 500/minute (5000/5mins)
# Trades valued under $200: max 500 per day, per account.
NUM_REQUESTS = 0
# GLOBAL VARIABLE THAT IS "True" WHEN I HIT RATELIMIT (500 REQ/MIN)
RATE_LIMIT_HIT = False


async def count_rate_limit():
    global NUM_REQUESTS
    global RATE_LIMIT_HIT
    # Save the current time to a variable ('t')
    t = datetime.now()
    while True:
        delta = datetime.now() - t
        if delta.seconds >= 60 and NUM_REQUESTS >= 500:
            print("WARNING!! RATE LIMIT WAS HIT. AFTER THIS TRADE SYS EXIT.")
            print(f"1 minute has elapsed and {NUM_REQUESTS} requests were done (rate limit statistics)")
            RATE_LIMIT_HIT = True
            # Update to new time
            t = datetime.now()
        await asyncio.sleep(1)


# LET OP CYTHON IMPLEMENTEREN !!!!!!!!!!!!!!!!!!!!!!!!!!

if __name__ == "__main__":
    client_ftx = FtxClient(api_key=api_key_ftx, api_secret=api_secret_ftx, subaccount_name=subaccount_name)

    asyncio.get_event_loop().run_until_complete(asyncio.wait(
        [websocket_phemex_orderstatus(), websocket_phemex_ticker(), websocket_ftx(), data_handler(),
         order_execution(), ]
    ))