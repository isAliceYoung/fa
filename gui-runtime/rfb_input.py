"""Bounded RFB 3.8 client stream, including fragmented resize messages.

Only the fixed authenticated gateway connects to the worker. This parser also
prevents a modified browser from requesting unbounded Xvnc framebuffer memory.
"""
import struct
import time

MAX_WIDTH, MAX_HEIGHT = 1600, 1000


class ClientInput:
    def __init__(self):
        self.pending=bytearray();self.handshake=0;self.last_resize=-1

    def feed(self, chunk, now=None):
        now=time.monotonic() if now is None else now
        self.pending.extend(chunk)
        if len(self.pending)>65536:raise ValueError('RFB input bound')
        output=bytearray()
        while self.pending:
            if self.handshake<3:
                size=12 if self.handshake==0 else 1
                if len(self.pending)<size:break
                packet=bytes(self.pending[:size]);del self.pending[:size]
                if self.handshake==0 and packet!=b'RFB 003.008\n':raise ValueError('RFB version')
                if self.handshake==1 and packet!=b'\x01':raise ValueError('RFB security')
                if self.handshake==2 and packet not in (b'\0',b'\1'):raise ValueError('RFB client init')
                self.handshake+=1;output.extend(packet);continue
            kind=self.pending[0]
            fixed={0:20,3:10,4:8,5:6,150:10,255:12}
            if kind in fixed:size=fixed[kind]
            elif kind==2:
                if len(self.pending)<4:break
                count=struct.unpack_from('>H',self.pending,2)[0]
                if count>256:raise ValueError('RFB encoding bound')
                size=4+count*4
            elif kind==248:
                if len(self.pending)<9:break
                size=9+self.pending[8]
                if size>73:raise ValueError('RFB fence bound')
            elif kind==6:
                if len(self.pending)<8:break
                size=8+abs(struct.unpack_from('>i',self.pending,4)[0])
                if size>4104:raise ValueError('Clipboard disabled/bounded')
            elif kind==251:
                if len(self.pending)<8:break
                if self.pending[6]!=1:raise ValueError('One bounded display required')
                size=24
            else:raise ValueError('Unsupported RFB input')
            if len(self.pending)<size:break
            packet=bytes(self.pending[:size]);del self.pending[:size]
            if kind==0 and (packet[4] not in (8,16,32) or packet[5]>packet[4]):raise ValueError('Pixel format bound')
            if kind==251:
                w,h=struct.unpack_from('>HH',packet,2)
                x,y,sw,sh=struct.unpack_from('>HHHH',packet,12)
                if not 640<=w<=MAX_WIDTH or not 480<=h<=MAX_HEIGHT or (x,y,sw,sh)!=(0,0,w,h):raise ValueError('Resize geometry bound')
                if self.last_resize>=0 and now-self.last_resize<.2:raise ValueError('Resize rate bound')
                self.last_resize=now
            if kind!=6:output.extend(packet)  # no clipboard bridge, including extended payloads
        return bytes(output)
