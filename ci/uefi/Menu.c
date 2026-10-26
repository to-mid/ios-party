/** @file
  Chiron menu input, based on UniXNU Menu.c at 7b52e7968c76453cdc18b5aa174fc368d5293b91.
  Copyright (c) 2026, UniXNU contributors. All rights reserved.
  SPDX-License-Identifier: GPL-2.0-or-later
  The original menu is BSD-2-Clause-Patent; this build links the GPL DSX parser.
**/
#include "Internal.h"
#include "ChironMenuTouch.h"

#define TICK_MS 20
#define DEFAULT_ROTATE_SEC 2

#ifndef SCAN_SUSPEND
#define SCAN_SUSPEND 0x0102
#endif

STATIC UINTN
CountInputDevices (VOID)
{
  EFI_STATUS Status;
  EFI_HANDLE *Handles;
  UINTN Count, Index, Physical = 0;
  VOID *Path;

  if (gST->ConIn == NULL) {
    return 0;
  }
  Status = gBS->LocateHandleBuffer (ByProtocol, &gEfiSimpleTextInProtocolGuid,
                                  NULL, &Count, &Handles);
  if (EFI_ERROR (Status)) {
    return 0;
  }
  for (Index = 0; Index < Count; Index++) {
    if (!EFI_ERROR (gBS->HandleProtocol (Handles[Index],
                        &gEfiDevicePathProtocolGuid, &Path))) {
      Physical++;
    }
  }
  FreePool (Handles);
  return Physical;
}

STATIC UINTN
TouchRow (UNIXNU_UI *Ui, UNIXNU_CONFIG *Config,
          EFI_ABSOLUTE_POINTER_STATE *State)
{
  UINTN X, Y, Row;
  if (Ui->Mode != UniXNUUiGraphics || !Ui->Width || !Ui->Height ||
      !Ui->RowHeight || State->CurrentX > 65535 || State->CurrentY > 65535) {
    return Config->EntryCount;
  }
  X = (UINTN)(State->CurrentX * (Ui->Width - 1) / 65535);
  Y = (UINTN)(State->CurrentY * (Ui->Height - 1) / 65535);
  if (X < Ui->MenuLeft || X - Ui->MenuLeft >= Ui->MenuWidth ||
      Y < Ui->MenuTop) {
    return Config->EntryCount;
  }
  Row = (Y - Ui->MenuTop) / Ui->RowHeight;
  if (Row >= Ui->VisibleRows || Row >= Config->EntryCount ||
      (Y - Ui->MenuTop) % Ui->RowHeight >= Ui->RowHeight - Ui->Scale) {
    return Config->EntryCount;
  }
  return Row;
}

UINTN
UniXNUMenuRun (UNIXNU_UI *Ui, UNIXNU_CONFIG *Config, BOOLEAN Countdown)
{
  EFI_STATUS Status;
  EFI_EVENT Timer, Events[2];
  EFI_INPUT_KEY Key;
  EFI_ABSOLUTE_POINTER_STATE Touch;
  UINTN EventCount, Signaled, Selected, Previous, Index, Count;
  UINTN RotateMs, RotateAccum = 0, ArmedRow, Row, TouchTicks = 0;
  BOOLEAN HasInput, UserActive = FALSE, TouchReady, Down = FALSE, Boot;
  INTN RemainingMs, LastShownSecond = -2, Second;
  CHAR16 Line[128];

  Count = Config->EntryCount;
  Selected = Config->DefaultIndex;
  ArmedRow = Count;
  UniXNUUiDrawFrame (Ui, Config);
  for (Index = 0; Index < Count; Index++) {
    UniXNUUiDrawEntry (Ui, Config, Index, Index == Selected);
  }
  /* No timer callback performs I2C. All transfers run in this menu loop. */
  TouchReady = Ui->Mode == UniXNUUiGraphics &&
               !EFI_ERROR (ChironMenuTouchStart ());
  HasInput = CountInputDevices () > 0 || TouchReady;
  if (!Countdown || Config->Timeout < 0) {
    RemainingMs = HasInput ? -1 : (INTN)Config->FallbackTimeout * 1000;
  } else {
    RemainingMs = Config->Timeout * 1000;
  }
  RotateMs = Config->RotateSeconds * 1000;
  if (!HasInput && RotateMs == 0) {
    RotateMs = DEFAULT_ROTATE_SEC * 1000;
  }
  UniXNUUiDrawStatus (Ui, 1, TouchReady ? L"Chiron touch" :
      L"Keyboard input");
  ChironMenuTouchStatus (Line, sizeof (Line));
  UniXNUUiDrawStatus (Ui, 2, Line);
  UniXNULog ("menu: chiron touch=%a, timeout=%d",
              TouchReady ? "ready" : "unavailable", (INT32)RemainingMs);

  Status = gBS->CreateEvent (EVT_TIMER, TPL_CALLBACK, NULL, NULL, &Timer);
  if (EFI_ERROR (Status)) {
    /* Never hand control to a kernel with an unclosed firmware I2C handle. */
    if (!EFI_ERROR (ChironMenuTouchStop ())) {
      return Selected;
    }
    UniXNUUiDrawStatus (Ui, 2, L"Touch close failed; kernel start blocked");
    CpuDeadLoop ();
  }
  gBS->SetTimer (Timer, TimerPeriodic, EFI_TIMER_PERIOD_MILLISECONDS (TICK_MS));
  EventCount = 0;
  Events[EventCount++] = Timer;
  if (gST->ConIn != NULL) {
    Events[EventCount++] = gST->ConIn->WaitForKey;
  }

  for (;;) {
    Second = RemainingMs < 0 ? -1 : (RemainingMs + 999) / 1000;
    if (Second != LastShownSecond) {
      if (RemainingMs >= 0) {
        UnicodeSPrint (Line, sizeof (Line), L"Booting %s in %u s",
            Config->Entries[Config->DefaultIndex].Title, (UINT32)Second);
      } else {
        UnicodeSPrint (Line, sizeof (Line), L"Select an entry");
      }
      UniXNUUiDrawStatus (Ui, 0, Line);
      LastShownSecond = Second;
    }
    Status = gBS->WaitForEvent (EventCount, Events, &Signaled);
    if (EFI_ERROR (Status)) {
      UniXNULog ("menu: WaitForEvent failed %r", Status);
      break;
    }
    Previous = Selected;
    Boot = FALSE;
    if (Signaled == 0) {
      if (TouchReady) {
        Status = ChironMenuTouchPoll (&Touch);
        if (!EFI_ERROR (Status)) {
          if (!UserActive) {
            UserActive = TRUE;
            RemainingMs = -1;
            Selected = Config->DefaultIndex;
          }
          Row = TouchRow (Ui, Config, &Touch);
          if (Touch.ActiveButtons & EFI_ABSP_TouchActive) {
            if (!Down) {
              ArmedRow = Row;
              if (Row < Count) {
                Selected = Row;
              }
            } else if (Row != ArmedRow) {
              ArmedRow = Count;
            }
            Down = TRUE;
          } else {
            Boot = Down && ArmedRow < Count && Row == ArmedRow;
            Down = FALSE;
            ArmedRow = Count;
          }
          ChironMenuTouchStatus (Line, sizeof (Line));
          UniXNUUiDrawStatus (Ui, 2, Line);
        } else if (Status != EFI_NOT_READY) {
          TouchReady = FALSE;
          Down = FALSE;
          ArmedRow = Count;
          RemainingMs = -1;
          UserActive = TRUE;
          ChironMenuTouchStatus (Line, sizeof (Line));
          UniXNUUiDrawStatus (Ui, 2, Line);
        }
        if (++TouchTicks % 10 == 0) {
          ChironMenuTouchFrameStatus (Line, sizeof (Line));
          UniXNUUiDrawStatus (Ui, 1, Line);
          if (TouchReady) {
            ChironMenuTouchStatus (Line, sizeof (Line));
            UniXNUUiDrawStatus (Ui, 2, Line);
          }
        }
      }
      if (RemainingMs == 0) {
        Selected = Config->DefaultIndex;
        Boot = TRUE;
      } else if (RemainingMs > 0) {
        RemainingMs -= TICK_MS;
        if (RemainingMs <= 0) {
          Selected = Config->DefaultIndex;
          Boot = TRUE;
        }
      }
      if (!UserActive && RotateMs > 0 && Count > 1) {
        RotateAccum += TICK_MS;
        if (RotateAccum >= RotateMs) {
          RotateAccum = 0;
          Selected = (Selected + 1) % Count;
        }
      }
    } else {
      if (EFI_ERROR (gST->ConIn->ReadKeyStroke (gST->ConIn, &Key))) {
        continue;
      }
      ArmedRow = Count;
      Down = FALSE;
      if (!UserActive) {
        UserActive = TRUE;
        RemainingMs = -1;
        Selected = Config->DefaultIndex;
      }
      if (Key.ScanCode == SCAN_UP || Key.UnicodeChar == L'k') {
        Selected = (Selected + Count - 1) % Count;
      } else if (Key.ScanCode == SCAN_DOWN || Key.UnicodeChar == L'j') {
        Selected = (Selected + 1) % Count;
      } else if (Key.ScanCode == SCAN_HOME) {
        Selected = 0;
      } else if (Key.ScanCode == SCAN_END) {
        Selected = Count - 1;
      } else if (Key.UnicodeChar >= L'1' && Key.UnicodeChar <= L'9' &&
                 (UINTN)(Key.UnicodeChar - L'1') < Count) {
        Selected = Key.UnicodeChar - L'1';
        Boot = TRUE;
      } else if (Key.UnicodeChar == CHAR_CARRIAGE_RETURN ||
                 Key.UnicodeChar == CHAR_LINEFEED || Key.UnicodeChar == L' ' ||
                 Key.ScanCode == SCAN_RIGHT || Key.ScanCode == SCAN_SUSPEND) {
        Boot = TRUE;
      }
    }
    if (Selected != Previous) {
      UniXNUUiDrawEntry (Ui, Config, Previous, FALSE);
      UniXNUUiDrawEntry (Ui, Config, Selected, TRUE);
    }
    if (Boot) {
      if (!EFI_ERROR (ChironMenuTouchStop ())) {
        break;
      }
      RemainingMs = -1;
      UserActive = TRUE;
      TouchReady = FALSE;
      UniXNUUiDrawStatus (Ui, 2, L"Touch close failed; kernel start blocked");
    }
  }
  gBS->SetTimer (Timer, TimerCancel, 0);
  gBS->CloseEvent (Timer);
  if (EFI_ERROR (ChironMenuTouchStop ())) {
    UniXNUUiDrawStatus (Ui, 2, L"Touch close failed; kernel start blocked");
    CpuDeadLoop ();
  }
  return Selected;
}
