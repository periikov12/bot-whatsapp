const makeWASocket = require('@whiskeysockets/baileys').default;
const { useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const axios = require('axios');
const fs = require('fs');
const http = require('http');

// --- SERVIDOR HTTP PARA RENDER (Mantiene el servicio vivo) ---
const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('Bot de WhatsApp funcionando\n');
});
const PORT = process.env.PORT || 3000;
server.listen(PORT, '0.0.0.0', () => {
    console.log(`🌐 Servidor HTTP escuchando en puerto ${PORT}`);
});

// --- CONEXIÓN CON PYTHON ---
const PYTHON_URL = process.env.PYTHON_URL || 'http://localhost:5000';

async function iniciarBot() {
    const { state, saveCreds } = await useMultiFileAuthState('sesion_auth');
    const sock = makeWASocket({ 
        auth: state, 
        printQRInTerminal: false,
        // Forzar versión de WhatsApp Web compatible con Render
        version: [2, 3000, 1015901307]
    });

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        // GENERACIÓN DEL QR COMO ENLACE WEB
        if (qr) {
            const urlQR = `https://api.qrserver.com/v1/create-qr-code/?size=500x500&data=${encodeURIComponent(qr)}`;
            console.log('\n\n======================================================');
            console.log('⚠️ COPIA Y PEGA ESTE ENLACE EN TU NAVEGADOR PARA ESCANEAR EL QR:');
            console.log(urlQR);
            console.log('======================================================\n\n');
        }

        if (connection === 'close') {
            const shouldReconnect = lastDisconnect.error?.output?.statusCode !== DisconnectReason.loggedOut;
            if (shouldReconnect) iniciarBot();
        } else if (connection === 'open') {
            console.log('✅ ¡Bot conectado a WhatsApp!');
        }
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('messages.upsert', async (m) => {
        const msg = m.messages[0];
        if (!msg.message || msg.key.fromMe) return;

        const texto = msg.message.conversation || msg.message.extendedTextMessage?.text;
        const chatId = msg.key.remoteJid;

        // --- COMANDO /nm ---
        if (texto && texto.startsWith('/nm ')) {
            const busqueda = texto.slice(4).trim();
            try {
                await sock.sendMessage(chatId, { text: `🔍 Buscando "${busqueda}"...` }, { quoted: msg });
                const response = await axios.post(`${PYTHON_URL}/nm`, { query: busqueda });
                if (response.data && response.data.success) {
                    const msgId = response.data.message_id;
                    let completado = false, intentos = 0;
                    while (!completado && intentos < 20) {
                        await new Promise(r => setTimeout(r, 1500));
                        const estado = await axios.get(`${PYTHON_URL}/nm_estado/${msgId}`);
                        if (estado.data.completado) {
                            completado = true;
                            const personas = estado.data.resultados;
                            if (personas.length === 0) {
                                await sock.sendMessage(chatId, { text: '❌ No se encontraron resultados.' }, { quoted: msg });
                            } else {
                                let rt = `✅ *${personas.length} ENCONTRADOS:*\n\n`;
                                personas.slice(0, 3).forEach(p => {
                                    rt += `👤 *DNI:* ${p.dni}\n`;
                                    p.campos.forEach(c => { rt += `▪️ ${c}\n`; });
                                    rt += `\n-------------------\n`;
                                });
                                if (personas.length > 3) rt += `... y ${personas.length - 3} más.`;
                                await sock.sendMessage(chatId, { text: rt }, { quoted: msg });
                            }
                        }
                        intentos++;
                    }
                }
            } catch (e) { await sock.sendMessage(chatId, { text: '⚠️ Error en app.py' }, { quoted: msg }); }
        }

        // --- COMANDO /c4 ---
        if (texto && texto.startsWith('/c4 ')) {
            const numero = texto.slice(4).trim();
            try {
                await sock.sendMessage(chatId, { text: `🔍 Buscando C4: ${numero}...` }, { quoted: msg });
                const response = await axios.post(`${PYTHON_URL}/c4`, { numero: numero });
                if (response.data && response.data.success) {
                    const msgId = response.data.message_id;
                    let completado = false, intentos = 0;
                    while (!completado && intentos < 20) {
                        await new Promise(r => setTimeout(r, 1500));
                        const estado = await axios.get(`${PYTHON_URL}/c4_estado/${msgId}`);
                        if (estado.data.completado) {
                            completado = true;
                            const imagenes = estado.data.imagenes || [];
                            if (imagenes.length > 0 && fs.existsSync(imagenes[0])) {
                                const buffer = fs.readFileSync(imagenes[0]);
                                await sock.sendMessage(chatId, { image: buffer, caption: `✅ *C4:* ${numero}` }, { quoted: msg });
                            } else {
                                await sock.sendMessage(chatId, { text: '❌ No se encontró imagen.' }, { quoted: msg });
                            }
                        }
                        intentos++;
                    }
                }
            } catch (e) { await sock.sendMessage(chatId, { text: '⚠️ Error en app.py' }, { quoted: msg }); }
        }

        // --- COMANDO /banca (Billeteras) ---
        if (texto && texto.startsWith('/banca ')) {
            const numero = texto.slice(7).trim();
            if (!/^\d{9}$/.test(numero)) {
                await sock.sendMessage(chatId, { text: '⚠️ El número de celular debe tener 9 dígitos.' }, { quoted: msg });
                return;
            }
            try {
                await sock.sendMessage(chatId, { text: `🔍 Buscando billeteras para el celular: ${numero}...` }, { quoted: msg });
                const response = await axios.post(`${PYTHON_URL}/billeteras`, { numero: numero });
                if (response.data && response.data.success) {
                    const msgId = response.data.message_id;
                    let completado = false, intentos = 0;
                    while (!completado && intentos < 20) {
                        await new Promise(r => setTimeout(r, 1500));
                        const estado = await axios.get(`${PYTHON_URL}/billeteras_estado/${msgId}`);
                        if (estado.data.completado) {
                            completado = true;
                            const texto_respuesta = estado.data.resultados;
                            await sock.sendMessage(chatId, { text: texto_respuesta }, { quoted: msg });
                        }
                        intentos++;
                    }
                    if (!completado) {
                        await sock.sendMessage(chatId, { text: '⏱️ Tiempo de espera agotado. Intenta de nuevo.' }, { quoted: msg });
                    }
                }
            } catch (e) {
                console.error(e);
                await sock.sendMessage(chatId, { text: '⚠️ Error en app.py' }, { quoted: msg });
            }
        }
    });
}

iniciarBot();
