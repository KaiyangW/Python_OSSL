'''
  Demo script for using PySerial to talk to the Cornerstone
  (or any RS-232 device).
'''

import sys
import time

# When you install pyserial, the important files wind up
# under a directory named "serial".
try:
  import serial
except ImportError:
  sys.path.append( "c:\Python37\Lib\site-packages\serial" ) # change to your path
  import serial

# Send a command.
def MyWrite( port, cmd_str ):
  cmd_str += '\n'
  # The python "serial" package appears to assume that strings are unicode unless
  # specifically encoded as binary, with the specifier b' prepended. This call
  # does that.
  wrt_str = cmd_str.encode( )
  port.write( wrt_str )

# Wait for and read a response.
def MyRead( port ):
  r = port.read_until( b'\n' )
  s = r.strip( )  # remove terminator characters
  d = s.decode( ) # remove b' at start and ' at end
  return d

# Send the query then wait for and read the response.
def MyQuery( port, qry_str, verbose=False ):
  if verbose:
    print( qry_str )
  MyWrite( port, qry_str )
  return MyRead( port )

if __name__ == '__main__':

  # Use Com1, 9600 baud, 2 second timeout, and defaults for everything else.
  port = serial.Serial( 'COM1', baudrate=9600, timeout=2 )
  # print( port )

  # Turn off the pesky echo that CornerstoneB comes with.
  # If echo was "on" then the Cornerstone will send this command back to us.
  # Note: echo is permanent in legacy Cornerstone.
  MyWrite( port, "echo 0" )

  # In case echo was on before we turned it off, get the "echo echo".
  # If echo was already off we will time out after two seconds and continue.
  print( "checking for echo response...")
  echo_str = MyRead( port )
  if( echo_str ):
    print( echo_str )

  # These queries are only valid in CornerstoneB.
  id_str = MyQuery( port, "*idn?" )
  print( id_str )

  err_str = MyQuery( port, "syst:err?" )
  print( err_str )

  port.close( )

