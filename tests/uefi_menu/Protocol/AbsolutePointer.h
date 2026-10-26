#ifndef MENU_TEST_ABSOLUTE_POINTER_H
#define MENU_TEST_ABSOLUTE_POINTER_H
#include "Internal.h"
#define EFI_ABSP_TouchActive 1
typedef struct {
  UINT64 CurrentX, CurrentY, CurrentZ;
  UINT32 ActiveButtons;
} EFI_ABSOLUTE_POINTER_STATE;
#endif
