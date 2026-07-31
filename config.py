SEARCH_CONFIG = {
    "keywords": "embedded firmware OR software engineer",
    "keywords_list": [
        # English — international / Europe
        "Firmware Engineer",
        "Senior Firmware Engineer",
        "Embedded Software Engineer",
        "Embedded Systems Engineer",
        "Embedded Firmware Engineer",
        "Electronic Design Engineer",
        "Firmware RT systems engineer",
        "Embedded Software Engineer - RTOS Development / Zephyr",
        "Lead Embedded Software Developer",
        # Spanish — LATAM / Spain
        "Ingeniero de Software Embebido",
        "Ingeniero Firmware",
        "Ingeniero de Sistemas Embebidos",
        "Desarrollador Firmware",
        "Desarrollador Software Embebido",
        "Ingeniero Electronico",
    ],
    "locations": [
        # LATAM
        "Argentina",
        "Uruguay",
        "Latin America",
        # Spain
        "Spain",
        "Sevilla",
        "Valencia",
        # Europe (remote)
        "European Union",
    ],
    "remote": ["2"],
    "experience": ["3", "4"],
    "job_type": ["F"],
    "limit_first_run": 100,
    "limit_incremental": 50,
    "filters": {
        "title_domain": ["embedded", "firmware", "hardware", "FPGA", "MCU", "RTOS",
                         "embebido", "electronico", "electrónico", "IoT"],
        "title_role": ["engineer", "ingeniero", "programador", "developer", "desarrollador",
                       "tech lead", "technical lead", "engineering manager", "development"],
        "title_exclude": ["intern", "stage", "junior", "backend", "frontend", "data"],
        "description_keywords": [
            "RTOS", "FreeRTOS", "SafeRTOS", "Zephyr", "Mbed OS", "RT-Thread",
            "C++", "Embedded C", "C", "C#",
            "Arduino", "ESP32", "ESP8266",
            "ARM Cortex-M",
            "Silabs", "STM32", "LPC", "NXP", "Microchip", "Atmel",
            "ARM", "Cortex-M", "MCU", "Microcontroller",
            "UART", "SPI", "I2C", "CAN", "RS485", "ADC", "DAC", "RS232",
            "firmware", "bare-metal", "hardware",
            "LoRa", "BLE", "NB-IoT", "Bluetooth", "Zigbee", "Sigfox",
            "FPGA", "VHDL", "Verilog", "MPLAB Harmony", 
            "low power", "IoT", "IEC 62304", "safety-critical", "Robotics",
        ],
        "description_exclude": [
            "React", "SQL", "Progress", "MLOps", "JavaScript",
            "Java", "iOS", "Ruby", "PHP", ".NET",
            "blockchain", "web developer", "frontend", "backend", "REST", "GraphQL", "FastAPI", "Django",
            "TypeScript", "Golang", "Angular", "Vue", "Node.js", "Express",
        ],
    }
}

WISHLIST_COMPANIES = [
    "BairesDev",
    "Globant",    
]

SCHEDULE_HOURS = 12