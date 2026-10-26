/* Minimal host ABI for executing the opt-in menu and its I2C adapter. */
#ifndef MENU_TEST_INTERNAL_H
#define MENU_TEST_INTERNAL_H
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <assert.h>
#define STATIC static
#define VOID void
#define EFIAPI
#define TRUE 1
#define FALSE 0
typedef uint8_t UINT8;
typedef uint16_t UINT16;
typedef uint32_t UINT32;
typedef uint64_t UINT64;
typedef uintptr_t UINTN;
typedef intptr_t INTN;
typedef int32_t INT32;
typedef int BOOLEAN;
typedef char CHAR8;
typedef wchar_t CHAR16;
typedef uint64_t EFI_STATUS;
typedef void *EFI_HANDLE;
typedef void *EFI_EVENT;
typedef struct { UINT32 a; UINT16 b, c; UINT8 d[8]; } EFI_GUID;
#define EFI_SUCCESS 0
#define EFI_ERROR(x) ((int64_t)(x) < 0)
#define EFI_UNSUPPORTED (UINT64_C(0x8000000000000000) | 3)
#define EFI_NOT_READY (UINT64_C(0x8000000000000000) | 6)
#define EFI_DEVICE_ERROR (UINT64_C(0x8000000000000000) | 7)
#define EFI_ALREADY_STARTED (UINT64_C(0x8000000000000000) | 20)
#define EVT_TIMER 0x80000000u
#define TPL_CALLBACK 8
#define TimerPeriodic 1
#define TimerCancel 0
#define ByProtocol 2
#define EFI_TIMER_PERIOD_MILLISECONDS(x) ((x) * 10000)
#define CONST const
#define FreePool free
#define ZeroMem(p,n) memset(p,0,n)
#define SCAN_UP 1
#define SCAN_DOWN 2
#define SCAN_RIGHT 3
#define SCAN_HOME 5
#define SCAN_END 6
#define CHAR_CARRIAGE_RETURN L'\r'
#define CHAR_LINEFEED L'\n'
typedef struct { UINT16 ScanCode; CHAR16 UnicodeChar; } EFI_INPUT_KEY;
typedef struct TEXT_IN TEXT_IN;
struct TEXT_IN {
  EFI_STATUS (*ReadKeyStroke)(TEXT_IN *, EFI_INPUT_KEY *);
  EFI_EVENT WaitForKey;
};
typedef struct { TEXT_IN *ConIn; } EFI_SYSTEM_TABLE;
typedef struct {
  UINT32 HorizontalResolution, VerticalResolution, PixelsPerScanLine;
} GOP_INFO;
typedef struct { GOP_INFO *Info; UINT64 FrameBufferBase; } GOP_MODE;
typedef struct { GOP_MODE *Mode; } EFI_GRAPHICS_OUTPUT_PROTOCOL;
typedef struct {
  EFI_STATUS (*LocateHandleBuffer)(UINTN, EFI_GUID *, VOID *, UINTN *, EFI_HANDLE **);
  EFI_STATUS (*HandleProtocol)(EFI_HANDLE, EFI_GUID *, VOID **);
  EFI_STATUS (*CreateEvent)(UINT32, UINTN, VOID *, VOID *, EFI_EVENT *);
  EFI_STATUS (*SetTimer)(EFI_EVENT, UINTN, UINT64);
  EFI_STATUS (*WaitForEvent)(UINTN, EFI_EVENT *, UINTN *);
  EFI_STATUS (*CloseEvent)(EFI_EVENT);
  EFI_STATUS (*LocateProtocol)(EFI_GUID *, VOID *, VOID **);
  EFI_STATUS (*Stall)(UINTN);
} EFI_BOOT_SERVICES;
extern EFI_BOOT_SERVICES *gBS;
extern EFI_SYSTEM_TABLE *gST;
extern EFI_GUID gEfiSimpleTextInProtocolGuid, gEfiDevicePathProtocolGuid;
extern EFI_GUID gEfiGraphicsOutputProtocolGuid;
typedef enum { UniXNUUiGraphics, UniXNUUiText, UniXNUUiHeadless } UNIXNU_UI_MODE;
typedef struct {
  UNIXNU_UI_MODE Mode;
  UINT32 Width, Height;
  UINTN Scale, MenuTop, RowHeight, MenuLeft, MenuWidth, VisibleRows;
} UNIXNU_UI;
typedef struct { CHAR16 *Title; } UNIXNU_ENTRY;
typedef struct {
  UINTN EntryCount, DefaultIndex, RotateSeconds, FallbackTimeout;
  INTN Timeout;
  UNIXNU_ENTRY *Entries;
} UNIXNU_CONFIG;
void UniXNULog(const CHAR8 *, ...);
UINTN UnicodeSPrint(CHAR16 *, UINTN, const CHAR16 *, ...);
void UniXNUUiDrawFrame(UNIXNU_UI *, UNIXNU_CONFIG *);
void UniXNUUiDrawEntry(UNIXNU_UI *, UNIXNU_CONFIG *, UINTN, BOOLEAN);
void UniXNUUiDrawStatus(UNIXNU_UI *, UINTN, const CHAR16 *);
static inline void CpuDeadLoop(void) { abort(); }
#endif
