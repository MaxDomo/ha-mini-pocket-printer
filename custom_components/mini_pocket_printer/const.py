"""Constantes de l'intégration Mini Pocket Printer."""

DOMAIN = "mini_pocket_printer"

CONF_ADDRESS = "address"
LOCAL_NAME = "Mini Pocket Printer"   # prefixe : couvre aussi "..._BLE"

# Matériel identifié : imprimante thermique Tronic (Lidl) 5890. Le nom annonce
# en Bluetooth reste générique, il est partage par plusieurs marques du même
# OEM ; le modèle renvoye par 10 FF 20 F0 vaut "A2Y".
MANUFACTURER = "Tronic"
MODEL = "5890"
SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"

CONF_QUEUE_LIMIT = "queue_limit"
DEFAULT_QUEUE_LIMIT = 5
CONF_TRANSPORT = "transport"
# Auto par défaut : USB s'il est configuré, puis Bluetooth classique, puis
# BLE. L'imprimante se deplace, le bon transport dépend de l'endroit.
DEFAULT_TRANSPORT = "auto"
TRANSPORTS = ["ble", "usb", "auto"]

CONF_USB_PATH = "usb_path"   # /dev/usb/lp0, /dev/ttyACM0 ou usb:001:007


CONF_JOB_TTL = "job_ttl"
DEFAULT_JOB_TTL = 0   # heures avant péremption d'un travail en attente, 0 = jamais

CONF_KEEP_AWAKE = "keep_awake"
DEFAULT_KEEP_AWAKE = 10   # minutes entre deux interrogations, 0 = aucune

SERVICE_PRINT_TEXT = "print_text"
SERVICE_PRINT_TABLE = "print_table"
SERVICE_PRINT_QR = "print_qr"
SERVICE_PRINT_BARCODE = "print_barcode"
SERVICE_PRINT_IMAGE = "print_image"
SERVICE_PRINT_WEATHER = "print_weather"
SERVICE_FEED = "feed"
SERVICE_CANCEL = "cancel"
