from aiogram.fsm.state import State, StatesGroup

class CaptchaVibes(StatesGroup):
    captcha_ae = State()

class GetWalletAddress(StatesGroup):
    waiting_for_address = State()

class NewDeal(StatesGroup):
    buyer_username = State()
    crypto_amount = State()
    fiat_amount = State()
    currency_selection = State()
    payment_details = State()

class BuyerAccept(StatesGroup):
    wallet_address = State()

class SellerDeposit(StatesGroup):
    tx_hash = State()

class SellerConfirm(StatesGroup):
    confirm = State()

class DebugStates(StatesGroup):
    waiting_for_deal_id = State()
    waiting_for_user_id = State()
