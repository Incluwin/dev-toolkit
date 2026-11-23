// Headless v83 login probe for tools\release\public-smoke.ps1 (WORLD_SYSTEMS_PLAN I4.1).
// It speaks the client side of the v83 protocol with the server jar's own cyphers, so the server builds the
// packets a real client gets at login and on every warp (SET_FIELD, SET_NPC_SCRIPTABLE, SPAWN_NPC, ...)
// and its -Dbotstory.stockClientAudit check can fire, without a game client on the desktop.
//
//   java -cp <tree>\target\Cosmic.jar PublicSmokeLogin.java <host> <loginPort> <user> <password> <seconds per map> <map>...
//
// Logs in, selects the account's first character on world 0 channel 1, enters the game, then sends
// "!warp <map>" for each map (the account's character must be a GM). Prints one line per step and every
// distinct NPC id the server spawned. Exit 0 = every step ran and the server kept the connection; 1 = a step
// failed or the server closed the connection.
import net.encryption.InitializationVector;
import net.encryption.MapleAESOFB;
import net.encryption.MapleCustomEncryption;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.net.InetAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

public class PublicSmokeLogin {
    static final short VERSION = 83;
    // Recv opcodes (client -> server) and send opcodes (server -> client), net.opcodes.*.
    static final int LOGIN_PASSWORD = 0x01, CHARLIST_REQUEST = 0x05, SERVERLIST_REQUEST = 0x0B, CHAR_SELECT = 0x13,
            PLAYER_LOGGEDIN = 0x14, PONG = 0x18, GENERAL_CHAT = 0x31;
    static final int LOGIN_STATUS = 0x00, CHARLIST = 0x0B, SERVER_IP = 0x0C, PING = 0x11, SET_FIELD = 0x7D,
            SPAWN_NPC = 0x101;

    static final Set<Integer> npcs = new TreeSet<>();

    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            System.err.println("usage: PublicSmokeLogin host loginPort user password secondsPerMap [map...]");
            System.exit(2);
        }
        String host = args[0];
        int loginPort = Integer.parseInt(args[1]);
        String user = args[2], password = args[3];
        int dwell = Integer.parseInt(args[4]);
        int[] maps = Arrays.stream(args, 5, args.length).mapToInt(Integer::parseInt).toArray();

        int charId;
        InetAddress channelHost;
        int channelPort;
        try (Conn login = new Conn(host, loginPort)) {
            login.send(LOGIN_PASSWORD, out -> {
                str(out, user);
                str(out, password);
                out.write(new byte[6]);
                out.write(new byte[]{1, 2, 3, 4});
            });
            byte[] status = login.await(LOGIN_STATUS, 15);
            if (status[2] != 0) fail("login refused, LOGIN_STATUS reason " + status[2]);
            say("logged in as " + user);
            login.send(SERVERLIST_REQUEST, out -> { });
            login.send(CHARLIST_REQUEST, out -> { out.write(0); out.write(0); out.write(0); });
            byte[] list = login.await(CHARLIST, 30);
            if (list[2] != 0 || list[3] < 1) fail("no character on world 0 (CHARLIST status " + list[2] + ", count " + list[3] + ")");
            charId = le(list).getInt(4);
            say("character id " + charId);
            login.send(CHAR_SELECT, out -> {
                int32(out, charId);
                str(out, "00155D000001");
                str(out, "00155D000001_0A1B2C3D");
            });
            ByteBuffer ip = le(login.await(SERVER_IP, 30));
            ip.position(4);
            byte[] addr = new byte[4];
            ip.get(addr);
            channelHost = InetAddress.getByAddress(addr);
            channelPort = Short.toUnsignedInt(ip.getShort());
            say("channel at " + channelHost.getHostAddress() + ":" + channelPort);
        }

        try (Conn game = new Conn(channelHost.getHostAddress(), channelPort)) {
            game.send(PLAYER_LOGGEDIN, out -> int32(out, charId));
            game.await(SET_FIELD, 60);
            say("entered the game");
            game.drain(dwell);
            for (int map : maps) {
                game.send(GENERAL_CHAT, out -> { str(out, "!warp " + map); out.write(0); });
                byte[] field = game.await(SET_FIELD, 60);
                int got = le(field).getInt(2 + 9);
                say("warped to " + got + (got == map ? "" : " (asked for " + map + ")"));
                game.drain(dwell);
            }
            if (game.closed) fail("the server closed the game connection");
        }
        say("npcs spawned: " + npcs);
        say("PASS");
    }

    static void say(String line) { System.out.println("probe: " + line); }

    static void fail(String why) {
        say("npcs spawned: " + npcs);
        say("FAIL " + why);
        System.exit(1);
    }

    static ByteBuffer le(byte[] b) { return ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN); }

    static void int32(ByteArrayOutputStream out, int v) { out.writeBytes(le(new byte[4]).putInt(v).array()); }

    static void str(ByteArrayOutputStream out, String s) {
        byte[] b = s.getBytes(StandardCharsets.US_ASCII);
        out.writeBytes(le(new byte[2]).putShort((short) b.length).array());
        out.writeBytes(b);
    }

    interface Body { void write(ByteArrayOutputStream out) throws IOException; }

    static InitializationVector iv(byte[] bytes) throws Exception {
        Constructor<InitializationVector> c = InitializationVector.class.getDeclaredConstructor(byte[].class);
        c.setAccessible(true);
        return c.newInstance((Object) bytes);
    }

    /** One connection: the hello, then a reader thread that answers PING and queues every other packet. */
    static final class Conn implements AutoCloseable {
        final Socket socket;
        final OutputStream out;
        final MapleAESOFB send, recv;
        final BlockingQueue<byte[]> inbox = new LinkedBlockingQueue<>();
        volatile boolean closed;

        Conn(String host, int port) throws Exception {
            socket = new Socket(host, port);
            socket.setTcpNoDelay(true);
            out = socket.getOutputStream();
            DataInputStream in = new DataInputStream(socket.getInputStream());
            byte[] hello = new byte[16];
            in.readFully(hello);
            ByteBuffer h = le(hello);
            if (h.getShort(2) != VERSION) fail("server speaks version " + h.getShort(2));
            byte[] serverRecv = Arrays.copyOfRange(hello, 7, 11), serverSend = Arrays.copyOfRange(hello, 11, 15);
            send = new MapleAESOFB(iv(serverRecv), VERSION);
            recv = new MapleAESOFB(iv(serverSend), (short) (0xFFFF - VERSION));
            Thread reader = new Thread(() -> {
                try {
                    while (true) {
                        int header = in.readInt();
                        byte[] packet = new byte[MapleAESOFB.getPacketLength(header)];
                        in.readFully(packet);
                        recv.crypt(packet);
                        MapleCustomEncryption.decryptData(packet);
                        int op = Short.toUnsignedInt(le(packet).getShort(0));
                        if (op == PING) {
                            send(PONG, o -> { });
                            continue;
                        }
                        if (op == SPAWN_NPC && packet.length >= 10) npcs.add(le(packet).getInt(6));
                        inbox.add(packet);
                    }
                } catch (Exception e) {
                    closed = true;
                }
            }, "probe-reader-" + port);
            reader.setDaemon(true);
            reader.start();
        }

        synchronized void send(int opcode, Body body) throws IOException {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            b.writeBytes(le(new byte[2]).putShort((short) opcode).array());
            body.write(b);
            byte[] packet = b.toByteArray();
            byte[] header = send.getPacketHeader(packet.length);
            MapleCustomEncryption.encryptData(packet);
            send.crypt(packet);
            out.write(header);
            out.write(packet);
            out.flush();
        }

        byte[] await(int opcode, int seconds) throws InterruptedException {
            long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(seconds);
            while (System.nanoTime() < end) {
                byte[] p = inbox.poll(200, TimeUnit.MILLISECONDS);
                if (p != null && Short.toUnsignedInt(le(p).getShort(0)) == opcode) return p;
                if (p == null && closed) fail("the server closed the connection while waiting for 0x" + Integer.toHexString(opcode));
            }
            fail("no 0x" + Integer.toHexString(opcode) + " within " + seconds + " s");
            return null;
        }

        void drain(int seconds) throws InterruptedException {
            long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(seconds);
            while (System.nanoTime() < end && !closed) inbox.poll(200, TimeUnit.MILLISECONDS);
            inbox.clear();
        }

        @Override
        public void close() throws IOException { socket.close(); }
    }
}
