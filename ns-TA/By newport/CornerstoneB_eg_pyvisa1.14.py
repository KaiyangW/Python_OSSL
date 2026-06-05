# This script contains an example Cornerstone B monochromator class.
# Its purpose is to show how to connect to the Cornerstone B
# using usb or a serial (com) port, connecting through pyvisa.
# The script starts running at __main__, below.
# Note: for serial see also pyserial_demo.py.

# It uses python 3.7, pyvisa 1.9.1, and NI-VISA version 18.5.

import sys
import time
import threading
import warnings

try:
  import pyvisa
except ImportError:
  # Change this to match your pyvisa installation path.
  sys.path.append( "C:\Python312\Lib\site-packages\pyvisa" ) 
  import pyvisa


# Find a CornerstoneB connected to the USB.
# The 'list_resources' function of the resource manager returns a list of strings
# identifying the instruments that visa sees. The Cornerstone CS130B string looks like:
# "USB0::0x1FDE::0x0006::nnnn::INSTR" ("nnnn" is the serial number).
# If the caller leaves the sernum_str parameter blank, any CornerstoneB on the usb will do.
def FindUsbCSB( rm, sernum_str="0", verbose=False ):
  found_unit = False
  addr_str = ""

  # Get a list of all the instruments pyvisa can find connected to the computer.
  instr_list = rm.list_resources( )

  if verbose:
    print( "usb instr list:", instr_list )

  # Examine each entry in the instrument list.
  for instr in instr_list :
    fields = instr.split('::')
    if verbose:
     print( fields, len(fields))

    if( len(fields) == 5 ):         # 5 fields in a usbtmc device entry of the instrument list.
      if( fields[0] == "USB0" ):      # ... should probably not care about the '0'...
        if( fields[1] == "0x1FDE" ):    # Newport
          if(( fields[2] == "0x0014" ) or( fields[2] == "0x0006")):    # CS260B or CS130B
            if( sernum_str == "0" ) or (( fields[ 3 ] == sernum_str )):
             addr_str = instr
             found_unit = True
             break   # Found one! Stop examining the instrument list 

  if verbose and found_unit:
    print( fields[0], fields[1], fields[2], addr_str )

  return found_unit, addr_str

# Find a monochromator attached to the USB bus.
def GetUsbUnit( rm, sernum_str="0", verbose=False ):
  bOk       = True
  thisUnit  = None

  if( sernum_str != "0" ) and verbose:
    print( "get looking for sn ", sernum_str )

  bOk, addr_str = FindUsbCSB( rm, sernum_str, verbose )

  if bOk:
    if verbose:
      print( "addr_str:", addr_str )

    thisUnit = rm.open_resource( addr_str )

  if verbose:
    if (bOk):
      print( "Found unit:", thisUnit )
    else:
      print( "unit not found!" )

  return bOk, thisUnit

# This doesn't find a monochromator as such, 
# just a com port that could be connected to a unit.
def GetRs232Unit( rm, comport="COM1", timeout_msec=1000, verbose=False ):
  bOk       = False
  thisUnit  = None

  thisUnit  = rm.open_resource( comport )

  if verbose:
    print( "Found unit:", thisUnit )

  if( thisUnit != None ):
    bOk = True
    thisUnit.stop_bits = visa.constants.StopBits.one
    thisUnit.baud_rate = 9600
    thisUnit.data_bits = 8
    thisUnit.timeout   = timeout_msec

  else:
    print( "oops")

  return bOk, thisUnit


'''
 This is an example implementation of a Cornerstone B mono class and some access member functions. 
 See the __main__ function (below) for an example of how to use this class.
'''
class Cornerstone_Mono:
  def __init__ ( self, rm, rem_ifc="usb", timeout_msec=1000, sernum_str="0", comport="COM1" ):

    if ( rem_ifc == "usb" ) or ( rem_ifc == "USB" ):
      self.bFound, self.unit = GetUsbUnit( rm, sernum_str )
    else:
      self.bFound, self.unit = GetRs232Unit( rm, comport, timeout_msec )

    if( self.bFound ):
      print( "Found unit:", self.unit )
      self.unit.timeout = timeout_msec
    else:
      print( "Did not find unit on", rem_ifc )

    # Use python's mutual exclusion feature to allow multiple threads to talk
    # safely with the unit. This is a real bacon-saver.
    self.lock = threading.Lock( )

  def CloseSession( self ):
    self.unit.close( )

  def CS_Found( self ):
    return self.bFound

  # Send a command string to the unit. 
  # Note: pyvisa's default terminator is the same as the Cornerstone's (CR-LF),
  # so we don't need to specify it.
  def SendCommand( self, cmd_str, verbose=False ):
    self.lock.acquire( )

    if verbose:
      print( "cmd:", cmd_str.strip( ))

    self.unit.write( cmd_str )

    self.lock.release( )

  def GetQueryResponse( self, qry_str, verbose=False ):
    self.lock.acquire( )

    # catch timeout error without completely exploding.
    try:
      if verbose:
        print( "query:", qry_str.strip( ))

      qry_response = self.unit.query( qry_str )
    except:
      qry_response = " "
      print( "possible timeout:", qry_str )

    self.lock.release( )

    # remove pesky carriage return and linefeed.
    qry_response = qry_response.strip( )

    return qry_response

  def GetID( self ):
    id_str = self.GetQueryResponse( "*idn?" )
    return id_str

  def GetErrors( self, verbose=False ):
    cmd_str  = "system:error?"
    err_str = self.GetQueryResponse( cmd_str, verbose )
    if( err_str == "0, No Error"):
      bHasError = False
    else:
      bHasError = True
    return bHasError, err_str

  def WaitOpc( self, verbose=False ):
    qry_str = "*opc?"
    err_str = self.GetQueryResponse( qry_str, verbose )

  def SetFilter( self, filter_num, verbose=False ):
    cmd_str = "filter %d" % filter_num
    self.SendCommand( cmd_str, verbose )

  def SelectOutput( self, out_num, verbose=False ):
    cmd_str = "outport %d" % out_num
    self.SendCommand( cmd_str, verbose )

  def SelectGrating( self, grating_number, verbose=False ):
    cmd_str = "grating %d" % grating_number
    self.SendCommand( cmd_str, verbose )

  def UnitIdle( self, verbose=False ):
    qry_str = "idle?"
    idle_str = self.GetQueryResponse( qry_str, verbose )
    idle_val = int( idle_str )
    if idle_val == 1:
      return True
    else:
      return False

  def WaitForIdle( self, verbose=False ):
    unit_idle = self.UnitIdle( )
    while not unit_idle:
      unit_idle = self.UnitIdle( )

'''
  Example code to use the Cornerstone B monochromator class.
  If a monochromator is connected to the USB, find it and send a couple of commands.
  If a monochromator is connected to the RS-232, find it and query its identification.
'''
if __name__ == '__main__':

  with warnings.catch_warnings( ):
    # The visa module throws some pesky warnings that clutter up the screen. 
    # Ignore all warnings.
    warnings.simplefilter( "ignore" )

    try:
      # First set up the resource manager to talk to instruments.
      rm  = pyvisa.ResourceManager( )

      # Set up a usb-connected monochromator. 
      # Give it a query timeout of 29 seconds.
      # Don't care about the serial number.
      usb_mono = Cornerstone_Mono( rm, rem_ifc="usb", timeout_msec=29000 )

      if( usb_mono.CS_Found( )):
        # See what we get for an ID.
        print( "ID:", usb_mono.GetID( ))

        # Go to a wavelength, wait for it to be there, then go to another.
        usb_mono.SendCommand( "gowave 400.0" , True )
        usb_mono.WaitOpc( )

        usb_mono.SendCommand( "gowave 550.0" , True )
        usb_mono.WaitOpc( )

        # See what errors may have occurred.
        bHasError, err_str = usb_mono.GetErrors( )
        if( bHasError ):
          print( err_str )
        else:
          print( "no errors" )

      # Set up a serial-connected monochromator with a timeout of 2 seconds.
      # If you have a com port with a different number, change it here.
      # (Note: Devices and Printers in the Windows Startup menu _should_ tell you the 
      # comport number of a usb-to-serial converter.)
      # Note: also see the pyserial_demo.py file.
      ser_mono = Cornerstone_Mono( rm, "serial", timeout_msec=2000, comport="COM1" )
      if( ser_mono.CS_Found( )):
        print( ser_mono.GetID( ))

    except( KeyboardInterrupt, SystemExit ): # if either a ctrl-C or call (somewhere) to sys.exit()
      print( "quitting" )

    finally:
      if( ser_mono.CS_Found( )):
        ser_mono.CloseSession( )
      if( usb_mono.CS_Found( )):
        usb_mono.CloseSession( )

      rm.close( )

