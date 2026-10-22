// TEST client uses the same bounded RFB stream batching supported by noVNC.
// Server queue/byte limits remain unchanged.
export function keyFrames(text){
 const frames=[];let pairs=[];
 for(const ch of text){
  const point=ch.codePointAt(0),key=point<=255?point:0x01000000|point;
  const b=Buffer.alloc(16);b[0]=b[8]=4;b[1]=1;b.writeUInt32BE(key,4);b.writeUInt32BE(key,12);pairs.push(b);
  if(pairs.length===256){frames.push(Buffer.concat(pairs));pairs=[];}
 }
 if(pairs.length)frames.push(Buffer.concat(pairs));return frames;
}
