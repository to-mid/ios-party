/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Chiron-only, synchronous menu adapter. No firmware timer or XNU worker. */
#include "Internal.h"
#include "ChironMenuTouch.h"
#include "chiron_dsx.h"
#include "chiron_f12.h"

/*
 * MSM8998 I2C.efi SHA256 1b1df5b767b488d8049455c70c3b3d92e45aca8a2d33a5c075c6201a4e836ff6.
 * Its legacy config has frequency/address/address-type (7-bit = 0), and Open
 * uses zero-based instance numbers. The binary consumes the first two fields.
 * Newer Qualcomm's six-word config/one-based enum is a different ABI.
 */
typedef struct { UINT32 Frequency, Address, AddressType; } MENU_I2C_CONFIG;
typedef struct {
  UINT64 Revision;
  UINT32 (EFIAPI *Open)(UINT32, VOID **);
  UINT32 (EFIAPI *Read)(VOID *, MENU_I2C_CONFIG *, UINT16, UINT16,
                       UINT8 *, UINT16, UINT32 *, UINT32);
  UINT32 (EFIAPI *Write)(VOID *, MENU_I2C_CONFIG *, UINT16, UINT16,
                        UINT8 *, UINT16, UINT32 *, UINT32);
  VOID *Transfer;
  UINT32 (EFIAPI *Close)(VOID *);
} MENU_I2C;

typedef struct {
  UINT32 PullDown, Mode, PinControl, Enabled;
  UINT8 Ok;
} MENU_VREG_STATE;
typedef struct {
  UINT64 Revision;
  VOID *Control, *SetLevel;
  EFI_STATUS (EFIAPI *GetLevel)(UINT32, UINT32, UINT32 *);
  VOID *SetMode, *Multiphase;
  EFI_STATUS (EFIAPI *GetStatus)(UINT32, UINT32, MENU_VREG_STATE *);
} MENU_VREG;
typedef struct MENU_SPMI MENU_SPMI;
struct MENU_SPMI {
  UINT64 Revision;
  UINT32 (EFIAPI *Read)(MENU_SPMI *, UINT32, UINT32, UINT32,
                       UINT8 *, UINT32, UINT32 *);
};
_Static_assert (sizeof (MENU_I2C_CONFIG) == 12, "MSM8998 legacy I2C config");
_Static_assert (offsetof (MENU_I2C, Close) == 40, "MSM8998 I2C close slot");
_Static_assert (sizeof (MENU_VREG_STATE) == 20, "MSM8998 VREG status ABI");
_Static_assert (offsetof (MENU_VREG, GetStatus) == 48, "VREG GetStatus slot");

STATIC EFI_GUID mI2cGuid =
  {0xb27ae8b1,0x3e10,0x4d07,{0xab,0x5c,0xeb,0x9a,0x6d,0xc6,0xfa,0x8f}};
STATIC EFI_GUID mVregGuid =
  {0x22d38d3d,0xe8b6,0x4f8f,{0x9c,0x26,0xbc,0xeb,0x07,0xd6,0xcb,0x68}};
STATIC EFI_GUID mSpmiGuid =
  {0xfa5f306b,0xf47d,0x4ac4,{0xa4,0x7d,0x88,0x2f,0x82,0x04,0xec,0x30}};
STATIC MENU_I2C *mI2c;
STATIC MENU_VREG *mVreg;
STATIC MENU_SPMI *mSpmi;
STATIC VOID *mHandle;
STATIC MENU_I2C_CONFIG mConfig = {400, 0x20, 0};
STATIC struct chiron_dsx mTouch;
STATIC struct chiron_f12_reader mReader;
STATIC BOOLEAN mPrepared, mReady, mSuppress;
STATIC UINT8 mPage;
STATIC CONST CHAR8 *mStage = "disabled";
STATIC UINT32 mTransport, mActual, mRequested, mAddress;
STATIC UINT32 mDown, mMove, mUp;
STATIC UINT32 mLogged;
STATIC INT32 mResult;
STATIC EFI_ABSOLUTE_POINTER_STATE mLast;
STATIC UINT16 mBitmap;
STATIC UINTN mFrameBytes;
STATIC UINT8 mObserved, mF01;
STATIC CHAR8 mRaw[17];

#ifndef CHIRON_MENU_MMIO_READ
#define CHIRON_MENU_MMIO_READ(Address) (*(volatile UINT32 *)(UINTN)(Address))
#define CHIRON_MENU_MMIO_WRITE(Address, Value) \
  (*(volatile UINT32 *)(UINTN)(Address) = (Value))
#endif

STATIC VOID
MenuBarrier (VOID)
{
#if defined (__aarch64__)
  __asm__ volatile ("dsb sy" ::: "memory");
#else
  __sync_synchronize ();
#endif
}

STATIC BOOLEAN
PowerReady (VOID)
{
  STATIC CONST UINT32 Ids[] = {5, 13}, Levels[] = {1848000, 1880000};
  STATIC CONST UINT32 Registers[] = {0x4540, 0x4d40};
  STATIC CONST UINT16 Controls[] = {0x0738, 0x0758};
  UINTN Index;
  UINT32 Level, Count;
  UINT8 Data[2];
  MENU_VREG_STATE State;
  EFI_STATUS Status;
  for (Index = 0; Index < 2; Index++) {
    ZeroMem (&State, sizeof (State));
    Level = 0;
    Status = mVreg->GetLevel (0, Ids[Index], &Level);
    if (EFI_ERROR (Status) ||
        EFI_ERROR (mVreg->GetStatus (0, Ids[Index], &State)) ||
        Level != Levels[Index] || State.Enabled != 1 || State.Ok != 1) {
      return FALSE;
    }
    Count = 0;
    if (mSpmi->Read (mSpmi, 1, 0, Registers[Index], Data, 2, &Count) ||
        Count != 2 || (UINT16)(Data[0] | (UINT16)Data[1] << 8) != Controls[Index]) {
      return FALSE;
    }
  }
  return (CHIRON_MENU_MMIO_READ (0x03932000) & 0x23c) == 0x200 &&
         (CHIRON_MENU_MMIO_READ (0x03932004) & 3) == 3;
}

STATIC BOOLEAN
ReleaseReset (VOID)
{
  UINT32 Config = CHIRON_MENU_MMIO_READ (0x03d59000);
  UINT32 Before = CHIRON_MENU_MMIO_READ (0x03d59004);
  if (!PowerReady () || (Config & 0x23c) != 0x200 ||
      ((Before & 3) != 0 && (Before & 3) != 3)) {
    return FALSE;
  }
  if ((Before & 3) == 0) {
    if (EFI_ERROR (gBS->Stall (5000)) || !PowerReady () ||
        CHIRON_MENU_MMIO_READ (0x03d59000) != Config ||
        CHIRON_MENU_MMIO_READ (0x03d59004) != Before) {
      return FALSE;
    }
    MenuBarrier ();
    CHIRON_MENU_MMIO_WRITE (0x03d59004, Before | 2);
    MenuBarrier ();
  }
  if (EFI_ERROR (gBS->Stall (200000))) {
    return FALSE;
  }
  return PowerReady () && CHIRON_MENU_MMIO_READ (0x03d59000) == Config &&
         (CHIRON_MENU_MMIO_READ (0x03d59004) & 3) == 3;
}

STATIC int
SelectPage (void *Context, uint8_t Slave, uint8_t Page)
{
  (VOID)Context;
  if (!mHandle || Slave != 0x20 || Page >= CHIRON_DSX_PAGES) {
    return CHIRON_DSX_INVALID;
  }
  mStage = "page";
  mAddress = (UINT32)Page << 8 | 0xff;
  mRequested = 1;
  mActual = 0;
  /* This exact firmware rejects timeout=0 before attempting a transfer. */
  mTransport = mI2c->Write (mHandle, &mConfig, 0xff, 1, &Page, 1, &mActual, 100);
  if (mTransport || mActual != 1) {
    return CHIRON_DSX_IO;
  }
  mPage = Page;
  return 0;
}

STATIC int
ReadRegister (void *Context, uint8_t Slave, uint8_t Reg, uint8_t *Data, size_t Bytes)
{
  (VOID)Context;
  if (!mHandle || Slave != 0x20 || !Data || !Bytes || Bytes > 80 ||
      Bytes > 0xffu - Reg) {
    return CHIRON_DSX_INVALID;
  }
  mStage = "read";
  mAddress = (UINT32)mPage << 8 | Reg;
  mRequested = (UINT32)Bytes;
  mActual = 0;
  mTransport = mI2c->Read (mHandle, &mConfig, Reg, 1, Data,
                          (UINT16)Bytes, &mActual, 100);
  return !mTransport && mActual == Bytes ? (int)Bytes : CHIRON_DSX_IO;
}

STATIC int
ReadAbsolute (void *Context, uint16_t Address, uint8_t *Data, size_t Bytes)
{
  int Result = SelectPage (Context, 0x20, Address >> 8);
  return Result ? Result : ReadRegister (Context, 0x20, (UINT8)Address, Data, Bytes);
}

STATIC int
WriteAbsolute (void *Context, uint16_t Address, uint8_t Value)
{
  int Result = SelectPage (Context, 0x20, Address >> 8);
  if (Result) {
    return Result;
  }
  mStage = "control";
  mAddress = Address;
  mRequested = 1;
  mActual = 0;
  mTransport = mI2c->Write (mHandle, &mConfig, (UINT8)Address, 1,
                           &Value, 1, &mActual, 100);
  return !mTransport && mActual == 1 ? 0 : CHIRON_DSX_IO;
}

STATIC int
Validate (void *Context, const struct chiron_dsx_functions *Functions,
          struct chiron_dsx_layout *Layout)
{
  (VOID)Context;
  if (!mPrepared) {
    mPrepared = TRUE;
    return chiron_f12_prepare (&mReader, Functions, Layout);
  }
  return chiron_f12_validate (&mReader, Functions, Layout);
}

STATIC void
Post (void *Context, uint32_t Type, uint32_t Finger, uint32_t X, uint32_t Y)
{
  (VOID)Context;
  if (Type == CHIRON_DSX_DOWN) mDown++;
  if (Type == CHIRON_DSX_MOVE) mMove++;
  if (Type == CHIRON_DSX_UP) mUp++;
  if (mLogged++ < 64) {
    UniXNULog ("chiron menu: %a slot=%u x=%u y=%u",
        Type == CHIRON_DSX_DOWN ? "DOWN" : Type == CHIRON_DSX_MOVE ? "MOVE" : "UP",
        Finger, X, Y);
  }
}

STATIC void
Observe (const struct chiron_dsx *Device, const uint8_t *Frame, size_t Bytes,
         uint16_t Before, uint16_t After, uint8_t F01, unsigned Observed, int Result)
{
  STATIC CONST CHAR8 Hex[] = "0123456789abcdef";
  UINTN Index, First = 0, Offset, Prefix;
  while (First < CHIRON_DSX_CONTACTS && !(Before & (1u << First))) First++;
  Offset = First < CHIRON_DSX_CONTACTS ? First * CHIRON_DSX_OBJECT_BYTES : 0;
  Prefix = Bytes >= Offset + 8 ? 8 : 0;
  (VOID)Device;
  (VOID)After;
  (VOID)Result;
  for (Index = 0; Index < Prefix; Index++) {
    mRaw[2 * Index] = Hex[Frame[Offset + Index] >> 4];
    mRaw[2 * Index + 1] = Hex[Frame[Offset + Index] & 15];
  }
  mRaw[2 * Prefix] = 0;
  mBitmap = Before;
  mFrameBytes = Bytes;
  mObserved = (UINT8)Observed;
  mF01 = F01;
}

EFI_STATUS
ChironMenuTouchStop (VOID)
{
  UINT32 Result;
  mReady = FALSE;
  chiron_dsx_stop (&mTouch);
  if (mHandle) {
    Result = mI2c->Close (mHandle);
    UniXNULog ("chiron menu: I2C close result=%u", Result);
    if (Result) {
      return EFI_DEVICE_ERROR;
    }
    mHandle = NULL;
  }
  return EFI_SUCCESS;
}

EFI_STATUS
ChironMenuTouchStart (VOID)
{
  EFI_GRAPHICS_OUTPUT_PROTOCOL *Gop;
  struct chiron_dsx_bus Bus = {NULL, SelectPage, ReadRegister, Validate};
  EFI_STATUS Status;
  UINTN Open;
  if (mHandle) {
    return EFI_ALREADY_STARTED;
  }
  mStage = "platform";
  mPrepared = mReady = mSuppress = FALSE;
  mDown = mMove = mUp = mTransport = mActual = mRequested = mAddress = 0;
  mLogged = mBitmap = mFrameBytes = 0;
  mObserved = mF01 = 0;
  mRaw[0] = 0;
  mResult = 0;
  ZeroMem (&mReader, sizeof (mReader));
  mLast = (EFI_ABSOLUTE_POINTER_STATE){0};
  Status = gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&Gop);
  if (EFI_ERROR (Status) || !Gop || !Gop->Mode || !Gop->Mode->Info ||
      Gop->Mode->FrameBufferBase != 0x9d400000 ||
      Gop->Mode->Info->HorizontalResolution != 1080 ||
      Gop->Mode->Info->VerticalResolution != 2160 ||
      Gop->Mode->Info->PixelsPerScanLine != 1080) {
    return EFI_UNSUPPORTED;
  }
  mStage = "I2C ABI";
  Status = gBS->LocateProtocol (&mI2cGuid, NULL, (VOID **)&mI2c);
  if (EFI_ERROR (Status) || !mI2c || mI2c->Revision != 0x10000) {
    return EFI_UNSUPPORTED;
  }
  Open = (UINTN)mI2c->Open;
  if (Open < 0x2378 || (UINTN)mI2c->Read != Open + 0x64 ||
      (UINTN)mI2c->Write != Open + 0x360 ||
      (UINTN)mI2c->Transfer != Open + 0x118 ||
      (UINTN)mI2c->Close != Open + 0x404) {
    return EFI_UNSUPPORTED;
  }
  mStage = "power";
  if (EFI_ERROR (gBS->LocateProtocol (&mVregGuid, NULL, (VOID **)&mVreg)) ||
      !mVreg || mVreg->Revision != 0x10005 || !mVreg->GetLevel || !mVreg->GetStatus ||
      EFI_ERROR (gBS->LocateProtocol (&mSpmiGuid, NULL, (VOID **)&mSpmi)) ||
      !mSpmi || mSpmi->Revision != 0x10000 || !mSpmi->Read || !ReleaseReset ()) {
    return EFI_NOT_READY;
  }
  mStage = "open";
  /* Open's table at RVA 0x8dd0: index 4 -> DAL I2C5 device 0x02000025. */
  mTransport = mI2c->Open (4, &mHandle);
  if (mTransport || !mHandle) {
    ChironMenuTouchStop ();
    return EFI_DEVICE_ERROR;
  }
  mReader = (struct chiron_f12_reader){
    .read = ReadAbsolute, .write = WriteAbsolute
  };
  chiron_dsx_init (&mTouch, &Bus, Post, NULL);
  mResult = chiron_dsx_start (&mTouch);
  if (mResult) {
    UniXNULog ("chiron menu: discovery=%d field=%a address=%x I2C=%u got=%u want=%u",
        mResult, mReader.refusal.field ? mReader.refusal.field : mStage,
        mAddress, mTransport, mActual, mRequested);
    ChironMenuTouchStop ();
    return EFI_DEVICE_ERROR;
  }
  mTouch.observer = Observe;
  mStage = "ready";
  mReady = TRUE;
  UniXNULog ("chiron menu: F12 data1=%x data15=%x contacts=%u max=%ux%u",
      mTouch.layout.data1, mTouch.layout.data15, mTouch.layout.contacts,
      mTouch.layout.max_x, mTouch.layout.max_y);
  return EFI_SUCCESS;
}

EFI_STATUS
ChironMenuTouchPoll (EFI_ABSOLUTE_POINTER_STATE *State)
{
  EFI_ABSOLUTE_POINTER_STATE Next = mLast;
  UINTN Finger, Active = 0, First = CHIRON_DSX_CONTACTS;
  if (!mReady || !State) {
    return EFI_DEVICE_ERROR;
  }
  mResult = chiron_dsx_poll (&mTouch);
  if (mResult) {
    mReady = FALSE;
    UniXNULog ("chiron menu: poll=%d field=%a address=%x I2C=%u got=%u want=%u",
        mResult, mReader.refusal.field ? mReader.refusal.field : mStage,
        mAddress, mTransport, mActual, mRequested);
    return EFI_DEVICE_ERROR;
  }
  for (Finger = 0; Finger < mTouch.layout.contacts; Finger++) {
    if (mTouch.contact[Finger].down) {
      if (First == CHIRON_DSX_CONTACTS) First = Finger;
      Active++;
    }
  }
  /* Multiple fingers never choose a menu action. Release/cancel the gesture. */
  if (Active > 1) {
    mSuppress = TRUE;
  } else if (Active == 0) {
    mSuppress = FALSE;
  }
  Next.ActiveButtons = Active == 1 && !mSuppress ? EFI_ABSP_TouchActive : 0;
  if (Next.ActiveButtons) {
    Next.CurrentX = mTouch.contact[First].x;
    Next.CurrentY = mTouch.contact[First].y;
  } else if (mSuppress) {
    Next.CurrentX = Next.CurrentY = 65535;
  }
  if (Next.ActiveButtons == mLast.ActiveButtons &&
      Next.CurrentX == mLast.CurrentX && Next.CurrentY == mLast.CurrentY) {
    return EFI_NOT_READY;
  }
  mLast = *State = Next;
  mStage = "ready";
  return EFI_SUCCESS;
}

VOID
ChironMenuTouchStatus (CHAR16 *Line, UINTN Bytes)
{
  if (mReady) {
    UnicodeSPrint (Line, Bytes, L"D%u M%u U%u %u,%u",
        mDown, mMove, mUp, (UINT32)mLast.CurrentX, (UINT32)mLast.CurrentY);
  } else {
    UnicodeSPrint (Line, Bytes, L"touch %a r%d i%u @%x %u/%u",
        mReader.refusal.field ? mReader.refusal.field : mStage,
        mResult, mTransport, mAddress, mActual, mRequested);
  }
}

VOID
ChironMenuTouchFrameStatus (CHAR16 *Line, UINTN Bytes)
{
  UnicodeSPrint (Line, Bytes, L"b%04x o%x f%02x %uB %a",
      (UINT32)mBitmap, (UINT32)mObserved, (UINT32)mF01, (UINT32)mFrameBytes, mRaw);
}
